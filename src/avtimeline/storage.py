from __future__ import annotations

import hashlib
import json
import math
import os
import tempfile
from pathlib import Path
from typing import Any

SCHEMA_VERSION = "1.0"


def read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path: Path, data: Any, *, exclusive: bool = False) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    text = json.dumps(data, ensure_ascii=False, indent=2, allow_nan=False) + "\n"
    fd, name = tempfile.mkstemp(dir=path.parent, suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(text)
        if exclusive:
            # Publish a complete document without replacing an existing baseline.
            os.link(name, path)
        else:
            os.replace(name, path)
    finally:
        Path(name).unlink(missing_ok=True)


def fingerprint(data: Any) -> str:
    encoded = json.dumps(data, ensure_ascii=False, sort_keys=True, allow_nan=False).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def file_hash(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def asset_path(work: Path, relative: str) -> Path:
    """Resolve bundle-local assets; never accept traversal or external paths."""
    path = (work / relative).resolve()
    if not path.is_relative_to(work.resolve()):
        raise ValueError(f"Asset is outside the work directory: {relative}")
    return path


def timestamp(seconds: float, *, srt: bool = False) -> str:
    millis = round(max(0, seconds) * 1000)
    hours, millis = divmod(millis, 3_600_000)
    minutes, millis = divmod(millis, 60_000)
    secs, millis = divmod(millis, 1000)
    return f"{hours:02}:{minutes:02}:{secs:02}{',' if srt else '.'}{millis:03}"


def valid_span(start: Any, end: Any, duration: float) -> None:
    if not all(type(v) in (int, float) and math.isfinite(v) for v in (start, end)):
        raise ValueError("Timestamps must be finite numbers")
    if not 0 <= start <= end <= duration + 0.1:
        raise ValueError(f"Invalid timestamp span {start}–{end} for duration {duration}")
