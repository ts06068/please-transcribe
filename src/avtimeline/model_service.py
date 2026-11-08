"""Docker lease controller, native supervisor, and single-model queue worker."""
from __future__ import annotations

import argparse
import importlib
import os
from pathlib import Path
import signal
import subprocess
import sys
import threading
import time
import uuid
import wave

from .storage import read_json, write_json
from .transcript import normalize, read_pcm_audio

LEASE_SECONDS = 8


def safe_read(path: Path) -> dict:
    try:
        return read_json(path)
    except (OSError, ValueError):
        return {}


def lease(control: Path) -> str | None:
    path = control / "lease.json"
    try:
        token = read_json(path).get("token", "")
        if 0 <= time.time() - path.stat().st_mtime < LEASE_SECONDS and len(token) == 32:
            return token
    except (OSError, ValueError):
        pass
    return None


def stop_event() -> threading.Event:
    event = threading.Event()
    for sig in (signal.SIGTERM, signal.SIGINT):
        signal.signal(sig, lambda *_: event.set())
    return event


def controller(control: Path) -> None:
    stopped = stop_event()
    token = uuid.uuid4().hex
    print("Requesting native ASR worker start", flush=True)
    try:
        while not stopped.is_set():
            write_json(control / "lease.json", {"token": token})
            stopped.wait(1)
    finally:
        if safe_read(control / "lease.json").get("token") == token:
            (control / "lease.json").unlink(missing_ok=True)
        deadline = time.monotonic() + 30
        while time.monotonic() < deadline:
            status = safe_read(control / "status.json")
            if status.get("state") == "stopped" and time.time() - status.get("updated", 0) < 8:
                print("Native ASR worker stopped", flush=True)
                return
            time.sleep(0.25)
        print("Stop acknowledgement pending; expired lease enforces shutdown", flush=True)


class Supervisor:
    def __init__(self, state: Path):
        self.state = state
        self.control = state / "control"
        self.process = None
        self.token = None
        self.retry_at = 0.0
        self.failures = 0
        self.log = (state / "worker.log").open("a", buffering=1)

    def publish(self, state: str, loaded: bool = False):
        write_json(self.control / "status.json", {"state": state, "model_loaded": loaded,
                   "token": self.token, "updated": time.time(),
                   "pid": self.process.pid if self.process else None})

    def stop(self):
        if self.process:
            self.publish("stopping")
            try:
                os.killpg(self.process.pid, signal.SIGTERM)
            except ProcessLookupError:
                pass
            try:
                self.process.wait(timeout=20)
            except subprocess.TimeoutExpired:
                pass
            try:
                os.killpg(self.process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            self.process.wait()
            self.process = None
        self.publish("stopped")

    def tick(self):
        token = lease(self.control)
        if token != self.token:
            self.stop()
            self.token, self.retry_at, self.failures = token, 0, 0
        if self.process and self.process.poll() is not None:
            self.stop()
            self.failures += 1
            self.retry_at = time.monotonic() + min(300, 30 * 2 ** min(self.failures - 1, 4))
        if token and self.process is None and time.monotonic() >= self.retry_at:
            self.process = subprocess.Popen([
                sys.executable, "-m", "avtimeline.model_service", "worker", "--state", str(self.state),
                "--token", token,
            ], stdout=self.log, stderr=subprocess.STDOUT, start_new_session=True)
        if self.process:
            status = safe_read(self.state / "queue/worker.json")
            state = status.get("state", "starting") if status.get("token") == token else "starting"
            self.publish(state, status.get("model_loaded", False) if status.get("token") == token else False)
        elif token:
            self.publish("error")
        else:
            self.publish("stopped")


def supervise(state: Path):
    import fcntl

    # One supervisor owns the worker and process group for this installation.
    with (state / "supervisor.lock").open("w") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        supervisor = Supervisor(state)
        stopped = stop_event()
        try:
            while not stopped.is_set():
                supervisor.tick()
                stopped.wait(1)
        finally:
            supervisor.stop()
            supervisor.log.close()


def process_job(directory: Path, engine, config: dict) -> None:
    request = read_json(directory / "request.json")
    job_id = request["id"]
    write_json(directory / "status.json", {"id": job_id, "state": "running"})
    try:
        with wave.open(str(directory / "audio.wav")) as audio:
            duration = audio.getnframes() / audio.getframerate()
        raw = engine(directory / "audio.wav", request)
        result = normalize(raw, duration, backend=config["backend"], model=config["model"])
        write_json(directory / "result.json", result)
        write_json(directory / "status.json", {"id": job_id, "state": "completed", "finished": time.time()})
        (directory / "audio.wav").unlink(missing_ok=True)
    except Exception as exc:
        # Detailed errors stay in the administrator's local log, not API responses.
        import traceback
        traceback.print_exc()
        write_json(directory / "status.json", {"id": job_id, "state": "failed",
                   "error": f"{type(exc).__name__}; see server worker.log", "finished": time.time()})
        (directory / "audio.wav").unlink(missing_ok=True)


def worker(state: Path, token: str):
    cfg = read_json(state / "server.json")
    status_path = state / "queue/worker.json"

    def publish(name, loaded=False):
        write_json(status_path, {"state": name, "model_loaded": loaded, "token": token})

    publish("starting")
    if cfg["backend"] == "mlx":
        import mlx.core as mx
        import mlx_whisper

        # MLX Whisper caches this model for subsequent transcribe calls. Pin/check
        # the installed API during provisioning; fail visibly if it changes.
        holder = importlib.import_module("mlx_whisper.transcribe").ModelHolder
        model_path = cfg.get("model_path", cfg["model"])
        holder.get_model(model_path, mx.float16)

        def engine(path, request):
            samples = read_pcm_audio(path)
            return mlx_whisper.transcribe(samples, path_or_hf_repo=model_path,
                language=None if request["language"] == "auto" else request["language"],
                task="transcribe", word_timestamps=True, verbose=None)
    else:
        from faster_whisper import WhisperModel

        model = WhisperModel(cfg["model"], device="cpu", compute_type="int8")

        def engine(path, request):
            segments, info = model.transcribe(read_pcm_audio(path), task="transcribe", word_timestamps=True,
                language=None if request["language"] == "auto" else request["language"],
                multilingual=request["multilingual"], vad_filter=False)
            return {"language": None if request["multilingual"] else info.language, "segments": [
                {"start": s.start, "end": s.end, "text": s.text, "avg_logprob": s.avg_logprob,
                 "words": [{"start": w.start, "end": w.end, "word": w.word, "probability": w.probability}
                           for w in s.words or []]} for s in segments]}
    stopped = stop_event()
    print(f"ASR model ready: {cfg['backend']} / {cfg['model']}", flush=True)
    while not stopped.is_set():
        publish("ready", True)
        for previous in (state / "queue/attempts").glob("*/status.json"):
            if time.time() - safe_read(previous).get("finished", time.time()) > cfg["retention_days"] * 86400:
                import shutil
                shutil.rmtree(previous.parent)
        requests = sorted((state / "queue/jobs").glob("*/request.json"), key=lambda p: p.stat().st_mtime)
        for path in requests:
            if stopped.is_set():
                break
            directory = path.parent
            status = safe_read(directory / "status.json")
            if status.get("state") in ("completed", "failed"):
                # Successful input audio is already removed. Keep text results
                # for the configured retention period so clients can resume.
                if time.time() - status.get("finished", time.time()) > cfg["retention_days"] * 86400:
                    import shutil
                    shutil.rmtree(directory)
                continue
            if (directory / "result.json").exists():
                write_json(directory / "status.json", {"id": path.parent.name, "state": "completed", "finished": time.time()})
                (directory / "audio.wav").unlink(missing_ok=True)
                continue
            if status.get("state") not in ("queued", "running") or not path.exists():
                continue
            publish("busy", True)
            process_job(directory, engine, cfg)
        stopped.wait(1)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("mode", choices=["controller", "supervise", "worker", "health"])
    parser.add_argument("--state", type=Path, required=True)
    parser.add_argument("--token", default="")
    args = parser.parse_args()
    if args.mode == "controller":
        controller(args.state / "control")
    elif args.mode == "supervise":
        supervise(args.state)
    elif args.mode == "worker":
        worker(args.state, args.token)
    else:
        status = safe_read(args.state / "control/status.json")
        sys.exit(0 if lease(args.state / "control") and status.get("state") in ("ready", "busy")
                 and time.time() - status.get("updated", 0) < 8 else 1)


if __name__ == "__main__":
    main()
