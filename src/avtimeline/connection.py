"""Portable client configuration and streaming, authenticated remote ASR."""
from __future__ import annotations

import hashlib
import http.client
import json
import os
from pathlib import Path
import time
import urllib.error
import urllib.parse
import urllib.request

from .storage import file_hash, read_json, write_json


class ServerHTTPError(ValueError):
    def __init__(self, status: int, message: str):
        self.status = status
        super().__init__(f"Server HTTP {status}: {message}")


def default_config() -> Path:
    base = Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config"))
    return base / "please-transcribe" / "client.json"


def credentials(endpoint: str | None = None, config: Path | None = None) -> dict:
    path = config or default_config()
    data = read_json(path) if path.exists() else {}
    url = endpoint or os.environ.get("PLEASE_TRANSCRIBE_ENDPOINT") or data.get("endpoint")
    key = os.environ.get("PLEASE_TRANSCRIBE_API_KEY") or data.get("api_key")
    parsed = urllib.parse.urlsplit(url or "")
    if parsed.scheme not in ("http", "https") or not parsed.hostname or parsed.username or parsed.password:
        raise ValueError("Configure an http(s) endpoint with connect; credentials must not be in its URL")
    if not isinstance(key, str) or not key or "\n" in key or "\r" in key:
        raise ValueError("Missing API key: use connect --api-key-file or PLEASE_TRANSCRIBE_API_KEY")
    return {"endpoint": url.rstrip("/"), "api_key": key}


def request(connection: dict, path: str, *, timeout: float = 30) -> dict:
    req = urllib.request.Request(connection["endpoint"] + path,
                                 headers={"Authorization": "Bearer " + connection["api_key"]})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as response:
            data = json.load(response)
        if path == "/v1/models" and (not isinstance(data, dict) or
                not isinstance(data.get("model"), str) or type(data.get("multilingual")) is not bool):
            raise ValueError("Endpoint is not a please-transcribe model API; check the server URL")
        if path == "/healthz" and (not isinstance(data, dict) or not isinstance(data.get("model"), dict) or
                data["model"].get("state") not in ("stopped", "starting", "ready", "busy", "stopping", "error")):
            raise ValueError("Endpoint returned an invalid transcription health response")
        return data
    except urllib.error.HTTPError as exc:
        try:
            message = json.loads(exc.read()).get("error", exc.reason)
        except (ValueError, UnicodeError):
            message = str(exc.reason)
        raise ServerHTTPError(exc.code, str(message)) from None


def save_connection(data: dict, path: Path | None = None, *, check: bool = True) -> Path:
    parsed = urllib.parse.urlsplit(data.get("endpoint", ""))
    if parsed.scheme not in ("http", "https") or not parsed.hostname or parsed.username or parsed.password:
        raise ValueError("Use an http(s) endpoint without credentials in its URL")
    if not isinstance(data.get("api_key"), str) or not data["api_key"] or any(c in data["api_key"] for c in "\r\n"):
        raise ValueError("Invalid API key")
    if check:
        request(data, "/v1/models")
    path = path or default_config()
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    # Create with private permissions before writing any secret data.
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as handle:
        os.chmod(path, 0o600)
        json.dump(data, handle, ensure_ascii=False, indent=2)
        handle.write("\n")
    return path


def upload(connection: dict, audio: Path, language: str, multilingual: bool,
           *, idempotency_key: str | None = None, retry_failed: bool = False) -> dict:
    parsed = urllib.parse.urlsplit(connection["endpoint"])
    cls = http.client.HTTPSConnection if parsed.scheme == "https" else http.client.HTTPConnection
    client = cls(parsed.hostname, parsed.port, timeout=300)
    query = urllib.parse.urlencode({"language": language, "multilingual": str(multilingual).lower()})
    try:
        client.putrequest("POST", parsed.path.rstrip("/") + "/v1/jobs?" + query)
        client.putheader("Authorization", "Bearer " + connection["api_key"])
        client.putheader("Content-Type", "audio/wav")
        client.putheader("Content-Length", str(audio.stat().st_size))
        if idempotency_key:
            client.putheader("Idempotency-Key", idempotency_key)
        if retry_failed:
            client.putheader("Retry-Failed", "true")
        client.endheaders()
        with audio.open("rb") as handle:
            for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                client.send(chunk)
        response = client.getresponse()
        data = json.loads(response.read())
        if response.status != 202:
            raise ValueError(f"Server HTTP {response.status}: {data.get('error', 'Upload failed')}")
        return data
    finally:
        client.close()


def remote_transcribe(work: Path, *, endpoint: str | None, config: Path | None,
                      model: str | None, language: str, multilingual: bool,
                      timeout: float = 86400, poll_interval: float = 2, progress=None,
                      retry_failed: bool = False) -> dict:
    connection = credentials(endpoint, config)
    capabilities = request(connection, "/v1/models")
    if model and model != capabilities["model"]:
        raise ValueError(f"Server serves {capabilities['model']}, not requested model {model}")
    if multilingual and not capabilities["multilingual"]:
        raise ValueError("This server does not support per-window language switching; choose auto or a language")
    audio = work / "audio.wav"
    identity = {"endpoint": connection["endpoint"], "audio_sha256": file_hash(audio),
                "language": language, "multilingual": multilingual, "model": capabilities["model"],
                "revision": capabilities.get("model_revision", capabilities["model"]),
                "principal": hashlib.sha256(connection["api_key"].encode()).hexdigest()}
    receipt_path = work / "remote-job.json"
    from .storage import fingerprint
    retried = False
    receipt = read_json(receipt_path) if receipt_path.exists() else {}
    if receipt.get("identity") != identity:
        receipt = {"identity": identity, "job_id": upload(connection, audio, language, multilingual,
                   idempotency_key=fingerprint(identity), retry_failed=retry_failed)["id"]}
        retried = retry_failed
        write_json(receipt_path, receipt)
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            status = request(connection, "/v1/jobs/" + receipt["job_id"])
        except ServerHTTPError as exc:
            if exc.status == 404 and retry_failed and not retried:
                upload(connection, audio, language, multilingual, idempotency_key=fingerprint(identity), retry_failed=True)
                retried = True
                continue
            raise
        if progress:
            progress(state=status["state"], job_id=receipt["job_id"])
        if status["state"] == "completed":
            return request(connection, "/v1/jobs/" + receipt["job_id"] + "/result")
        if status["state"] == "failed":
            if retry_failed and not retried:
                upload(connection, audio, language, multilingual, idempotency_key=fingerprint(identity), retry_failed=True)
                retried = True
                continue
            raise ValueError(f"Remote job failed: {status.get('error', 'Unknown error')}; "
                             "fix the server issue, then use --retry-failed to submit one new attempt")
        time.sleep(poll_interval)
    raise ValueError("Remote job is still pending; rerun transcribe to resume polling without uploading again")
