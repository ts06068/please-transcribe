"""Resumable URL intake. Local inputs remain untouched."""
from __future__ import annotations

from pathlib import Path
import re
import json
import subprocess
import sys
import threading
import time
import urllib.parse

from .storage import file_hash, fingerprint, read_json, write_json


def is_url(source: str) -> bool:
    return urllib.parse.urlsplit(str(source)).scheme in ("http", "https")


def resolve(source: str, work: Path, progress=None) -> Path:
    if not is_url(source):
        path = Path(source).expanduser().resolve()
        if not path.is_file():
            raise ValueError(f"Input recording does not exist: {path}")
        return path
    parsed = urllib.parse.urlsplit(source)
    if not parsed.hostname or parsed.username or parsed.password:
        raise ValueError("Use a valid http(s) URL without embedded credentials")
    target = work / "intake"
    target.mkdir(parents=True, exist_ok=True)
    receipt_path = target / "receipt.json"
    identity = fingerprint(source)
    pending = target / "input.json"
    if pending.exists() and read_json(pending)["input"] != identity:
        raise ValueError("Unfinished intake belongs to a different URL")
    write_json(pending, {"input": identity})
    if receipt_path.exists():
        receipt = read_json(receipt_path)
        path = (target / receipt["file"]).resolve()
        if not path.is_relative_to(target.resolve()):
            raise ValueError("Invalid cached download path")
        if receipt["input"] != identity:
            raise ValueError("This session belongs to a different URL; choose another work directory")
        if path.is_file() and file_hash(path) == receipt["sha256"]:
            return path
        if path.is_file():
            # Keep the corrupted bytes for inspection, but let the downloader
            # obtain a fresh copy instead of treating it as already complete.
            import uuid
            path.rename(path.with_name(path.name + ".corrupt-" + uuid.uuid4().hex[:8]))
    try:
        import yt_dlp  # noqa: F401
    except ImportError:
        raise ValueError("URL inputs need the url extra: python -m pip install '.[url]'") from None
    # yt-dlp resumes .part files and performs the platform-specific extraction.
    # URL query strings and credentials are never copied into public manifests.
    command = [sys.executable, "-m", "yt_dlp", "--no-playlist", "--no-warnings", "--newline",
               "--continue", "--paths", str(target.resolve()), "--output", "source.%(ext)s",
               "--merge-output-format", "mp4", "--print", "after_move:filepath", "--", source]
    process = None
    # Downloader progress can contain signed URLs; expose only the stage heartbeat.
    stopped = threading.Event()
    reporter = threading.Thread(target=progress.heartbeat, args=("download", stopped, time.monotonic()), daemon=True) if progress else None
    if reporter:
        reporter.start()
    try:
        metadata_command = [sys.executable, "-m", "yt_dlp", "--dump-single-json", "--skip-download",
                            "--flat-playlist", "--playlist-end", "2", "--no-playlist", "--no-warnings", "--", source]
        process = subprocess.Popen(metadata_command, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        metadata_text, _ = process.communicate()
        if process.returncode:
            raise ValueError("URL inspection failed; check availability and downloader version")
        metadata = json.loads(metadata_text)
        if metadata.get("_type") in ("playlist", "multi_video"):
            raise ValueError("This URL contains multiple recordings; supply individual video URLs to batch")
        process = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        stdout, stderr = process.communicate()
    except BaseException:
        if process and process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait()
        raise
    finally:
        stopped.set()
        if reporter:
            reporter.join()
    if process.returncode:
        raise ValueError("URL download failed. Check URL availability, yt-dlp version, and FFmpeg; "
                         "private URL details were omitted from the error.")
    lines = [line.strip() for line in stdout.splitlines() if line.strip()]
    if len(lines) != 1:
        raise ValueError("Expected exactly one recording; use individual video URLs for batch processing")
    path = Path(lines[-1]).resolve()
    if not path.is_file() or not path.is_relative_to(target.resolve()):
        raise ValueError("Downloader returned an invalid recording path")
    write_json(receipt_path, {"input": identity, "file": path.name, "sha256": file_hash(path),
                             "origin": f"{parsed.scheme}://{parsed.hostname}{parsed.path}"})
    return path


def session_name(source: str) -> str:
    stem = Path(urllib.parse.urlsplit(source).path).stem if is_url(source) else Path(source).stem
    stem = re.sub(r"[^\w.-]+", "-", stem, flags=re.UNICODE).strip(".-")[:60] or "recording"
    return stem + "-" + fingerprint(source)[:10]
