"""Small asynchronous WAV transcription API. Model execution stays in a worker."""
from __future__ import annotations

import argparse
import hashlib
import hmac
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import re
import shutil
import threading
import time
import urllib.parse
import uuid
import wave

from .storage import read_json, write_json


def model_status(control: Path) -> dict:
    try:
        status = read_json(control / "status.json")
        if time.time() - status["updated"] > 10:
            return {"state": "stopped"}
        return {k: v for k, v in status.items() if k in ("state", "model_loaded", "updated")}
    except (OSError, ValueError, KeyError):
        return {"state": "stopped"}


class API(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self, address, *, queue: Path, control: Path, keys: Path, config: dict):
        super().__init__(address, Handler)
        self.queue, self.control, self.keys, self.config = queue, control, keys, config
        self.jobs = queue / "jobs"
        self.jobs.mkdir(parents=True, exist_ok=True)
        # This is a single API instance. Upload handlers from a previous process
        # cannot survive its restart; unpublished reservations are safe to remove.
        for directory in self.jobs.iterdir():
            if re.fullmatch(r"[0-9a-f]{32}", directory.name) and directory.is_dir() and not (directory / "request.json").exists():
                shutil.rmtree(directory)
        self.submission_lock = threading.Lock()


class Handler(BaseHTTPRequestHandler):
    server: API

    def log_message(self, *_):
        pass  # Do not log tokens, uploaded speech, or response bodies.

    def reply(self, status: int, data: dict) -> None:
        body = json.dumps(data, ensure_ascii=False, allow_nan=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("Connection", "close")
        self.end_headers()
        self.wfile.write(body)
        self.close_connection = True

    def principal(self) -> str | None:
        supplied = self.headers.get("Authorization", "")
        try:
            keys = read_json(self.server.keys)
        except (OSError, ValueError):
            self.reply(503, {"error": "Authentication configuration unavailable"})
            return None
        for name, key in keys.items():
            if hmac.compare_digest(supplied.encode(), ("Bearer " + key).encode()):
                return name
        self.reply(401, {"error": "Invalid API key"})
        return None

    def job(self, job_id: str, principal: str) -> Path | None:
        if not re.fullmatch(r"[0-9a-f]{32}", job_id):
            self.reply(404, {"error": "Job not found"})
            return None
        directory = self.server.jobs / job_id
        try:
            if read_json(directory / "request.json")["owner"] == principal:
                return directory
        except (OSError, ValueError, KeyError):
            pass
        self.reply(404, {"error": "Job not found"})
        return None

    def do_GET(self):
        principal = self.principal()
        if principal is None:
            return
        path = urllib.parse.urlsplit(self.path).path
        if path == "/healthz":
            self.reply(200, {"api": "ready", "model": model_status(self.server.control)})
        elif path == "/v1/models":
            cfg = self.server.config
            self.reply(200, {"model": cfg["model"], "backend": cfg["backend"],
                             "multilingual": cfg["backend"] == "faster-whisper",
                             "max_upload_bytes": cfg["max_upload_bytes"]})
        elif match := re.fullmatch(r"/v1/jobs/([0-9a-f]{32})(/result)?", path):
            directory = self.job(match[1], principal)
            if directory is None:
                return
            try:
                if match[2]:
                    if read_json(directory / "status.json")["state"] != "completed":
                        self.reply(409, {"error": "Result is not ready"})
                    else:
                        self.reply(200, read_json(directory / "result.json"))
                else:
                    self.reply(200, read_json(directory / "status.json"))
            except (OSError, ValueError):
                self.reply(503, {"error": "Job state unavailable"})
        else:
            self.reply(404, {"error": "Route not found"})

    def do_POST(self):
        self.connection.settimeout(300)
        principal = self.principal()
        if principal is None:
            return
        parsed = urllib.parse.urlsplit(self.path)
        if parsed.path != "/v1/jobs":
            self.reply(404, {"error": "Route not found"})
            return
        query = urllib.parse.parse_qs(parsed.query)
        language = query.get("language", ["auto"])[0]
        multi = query.get("multilingual", ["false"])[0]
        if not re.fullmatch(r"auto|[a-z]{2,3}", language) or multi not in ("true", "false"):
            self.reply(422, {"error": "Invalid language or multilingual option"})
            return
        if multi == "true" and self.server.config["backend"] != "faster-whisper":
            self.reply(422, {"error": "This backend does not support per-window language switching"})
            return
        if model_status(self.server.control)["state"] not in ("starting", "ready", "busy"):
            self.reply(503, {"error": "Model is stopped; ask the server administrator to start it"})
            return
        try:
            size = int(self.headers.get("Content-Length", "0"))
        except ValueError:
            size = 0
        if size <= 0 or size > self.server.config["max_upload_bytes"] or self.headers.get("Transfer-Encoding"):
            self.reply(413, {"error": "Upload needs a valid Content-Length within the server limit"})
            return
        with self.server.submission_lock:
            idempotency = self.headers.get("Idempotency-Key")
            if idempotency and not re.fullmatch(r"[0-9a-f]{64}", idempotency):
                self.reply(422, {"error": "Invalid Idempotency-Key"})
                return
            job_id = (hashlib.sha256((principal + "\0" + idempotency).encode()).hexdigest()[:32]
                      if idempotency else uuid.uuid4().hex)
            directory = self.server.jobs / job_id
            retry_previous = False
            if directory.exists():
                try:
                    previous = read_json(directory / "request.json")
                except (OSError, ValueError):
                    self.reply(409, {"error": "An upload with this key is still in progress; retry later"})
                    return
                if previous["language"] != language or previous["multilingual"] != (multi == "true"):
                    self.reply(409, {"error": "This idempotency key belongs to different request options"})
                    return
                state = read_json(directory / "status.json")["state"]
                if state == "failed" and self.headers.get("Retry-Failed") == "true":
                    retry_previous = True
                else:
                    # Drain a retransmission, retaining the existing model job.
                    remaining = size
                    while remaining:
                        block = self.rfile.read(min(remaining, 1024 * 1024))
                        if not block:
                            self.reply(422, {"error": "Incomplete retry upload"})
                            return
                        remaining -= len(block)
                    self.reply(202, {"id": job_id, "state": state})
                    return
            pending = 0
            for path in self.server.jobs.glob("*/status.json"):
                try:
                    pending += read_json(path)["state"] in ("uploading", "queued", "running")
                except (OSError, ValueError, KeyError):
                    continue
            if pending >= self.server.config["max_pending"]:
                self.reply(429, {"error": "Transcription queue is full"})
                return
            if retry_previous:
                history = self.server.queue / "attempts"
                history.mkdir(exist_ok=True)
                directory.rename(history / (job_id + "-" + uuid.uuid4().hex))
            directory.mkdir(mode=0o700)
            write_json(directory / "status.json", {"id": job_id, "state": "uploading"})
        try:
            with (directory / "audio.wav").open("wb") as handle:
                remaining = size
                while remaining:
                    block = self.rfile.read(min(remaining, 1024 * 1024))
                    if not block:
                        raise ValueError("Incomplete upload")
                    handle.write(block)
                    remaining -= len(block)
            with wave.open(str(directory / "audio.wav")) as audio:
                if (audio.getnchannels(), audio.getsampwidth(), audio.getframerate()) != (1, 2, 16000):
                    raise ValueError("Expected mono 16 kHz PCM16 WAV from prepare")
                if not 0 < audio.getnframes() * 2 <= size:
                    raise ValueError("Invalid WAV length")
            write_json(directory / "status.json", {"id": job_id, "state": "queued"})
            # Publish the request last: the worker never sees partial uploads.
            write_json(directory / "request.json", {"id": job_id, "owner": principal,
                       "language": language, "multilingual": multi == "true", "created": time.time()})
            self.reply(202, {"id": job_id, "state": "queued"})
        except (OSError, ValueError, EOFError, wave.Error):
            shutil.rmtree(directory, ignore_errors=True)
            self.reply(422, {"error": "Invalid or incomplete WAV upload"})


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--state", type=Path, required=True)
    args = parser.parse_args()
    cfg = read_json(args.state / "server.json")
    API(("0.0.0.0", cfg["port"]), queue=args.state / "queue", control=args.state / "control",
        keys=args.state / "api-keys.json", config=cfg).serve_forever()


if __name__ == "__main__":
    main()
