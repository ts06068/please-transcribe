"""Shared Python/CLI/MCP operations for bounded audio/video investigation."""
from __future__ import annotations

import math
from pathlib import Path
import wave

from .storage import asset_path, file_hash, fingerprint, read_json, valid_span, write_json


def load(work: Path) -> dict:
    data = read_json(work / "timeline.json")
    if fingerprint({k: v for k, v in data.items() if k != "snapshot"}) != data["snapshot"]:
        raise ValueError("Timeline integrity check failed; rebuild from the source artifacts")
    for path, key in (("transcript.raw.json", "transcript"), ("visual.json", "visual"), ("manifest.json", "manifest")):
        if read_json(work / path) != data[key]:
            raise ValueError(f"{path} changed; build a fresh timeline and review bundle")
    return data


def inspection_frames(work: Path, snapshot: str) -> list[dict]:
    path = work / "inspection/evidence.json"
    entries = read_json(path).get("frames", []) if path.exists() else []
    return [entry for entry in entries if entry["snapshot"] == snapshot]


def get_context(work: Path, start: float, end: float, *, context: float = 3,
                max_frames: int = 12, max_segments: int = 100, offset: int = 0) -> dict:
    data = load(work)
    duration = data["manifest"]["duration"]
    valid_span(start, end, duration)
    if end - start > 300 or not 0 <= context <= 30:
        raise ValueError("Query at most 300 seconds, with context between zero and 30 seconds")
    if not 1 <= max_frames <= 120 or not 1 <= max_segments <= 1000 or offset < 0:
        raise ValueError("Invalid query limits")
    lo, hi = max(0, start - context), min(duration, end + context)
    segments = [s for s in data["transcript"]["segments"] if s["end"] >= lo and s["start"] <= hi]
    frames = sorted(data["visual"]["frames"] + inspection_frames(work, data["snapshot"]), key=lambda f: f["time"])
    selected = [f for f in frames if lo <= f["time"] <= hi]
    preceding = [f for f in frames if f["time"] < lo]
    if preceding:
        selected.insert(0, preceding[-1])
    events = [e for e in data["visual"]["events"] if lo <= e["time"] <= hi]
    evidence_ids = {fid for event in events for fid in event["frame_ids"]}
    selected_ids = {f["id"] for f in selected}
    selected += [f for f in frames if f["id"] in evidence_ids and f["id"] not in selected_ids]
    selected.sort(key=lambda f: f["time"])
    frame_count = len(selected)
    if frame_count > max_frames:
        # Preserve temporal coverage without pretending unreturned frames were seen.
        indices = [round(i * (frame_count - 1) / (max_frames - 1)) for i in range(max_frames)] if max_frames > 1 else [0]
        selected = [selected[index] for index in indices]
    return {"snapshot": data["snapshot"], "start": start, "end": end,
            "context_start": lo, "context_end": hi, "segments": segments[offset:offset + max_segments],
            "segment_total": len(segments),
            "next_segment_offset": offset + max_segments if offset + max_segments < len(segments) else None,
            "frames": selected, "frame_total": frame_count, "omitted_frames": frame_count - len(selected),
            "events": events, "note": "Temporal context only. Inspect actual media before making semantic claims."}


def clip_audio(work: Path, start: float, end: float, *, max_seconds: float = 120) -> dict:
    data = load(work)
    valid_span(start, end, data["manifest"]["duration"])
    if not 0 < end - start <= max_seconds:
        raise ValueError(f"Audio excerpts must be longer than zero and at most {max_seconds} seconds")
    with wave.open(str(work / "audio.wav")) as source:
        rate = source.getframerate()
        first = min(source.getnframes(), max(0, math.floor(start * rate)))
        last = min(source.getnframes(), math.ceil(end * rate))
        if last <= first:
            return {"snapshot": data["snapshot"], "available": False, "reason": "No audio samples in this range"}
        identity = fingerprint({"snapshot": data["snapshot"], "first": first, "last": last})[:20]
        relative = f"inspection/audio/{identity}.wav"
        path = asset_path(work, relative)
        path.parent.mkdir(parents=True, exist_ok=True)
        source.setpos(first)
        with wave.open(str(path), "wb") as output:
            output.setparams(source.getparams())
            remaining = last - first
            while remaining:
                count = min(remaining, rate * 10)
                block = source.readframes(count)
                if not block:
                    raise ValueError("Source audio is truncated")
                output.writeframes(block)
                remaining -= len(block) // (source.getnchannels() * source.getsampwidth())
    result = {"snapshot": data["snapshot"], "available": True, "audio": relative,
              "start": first / rate, "end": last / rate, "sha256": file_hash(path)}
    write_json(path.with_suffix(".json"), result)
    return result


def source_path(work: Path, data: dict, override: Path | None = None) -> Path:
    if override:
        path = override.expanduser().resolve()
    elif (work / "source.json").exists():
        path = Path(read_json(work / "source.json")["path"])
    elif (work / "pipeline.json").exists() and read_json(work / "pipeline.json").get("source_path"):
        path = Path(read_json(work / "pipeline.json")["source_path"])
    else:
        raise ValueError("Original video path is unavailable; provide --source")
    if not path.is_file():
        raise ValueError("Original video is unavailable; reconnect its cloud mount or provide --source")
    if file_hash(path) != data["manifest"]["source"]["sha256"]:
        raise ValueError("Original video does not match this session's source hash")
    return path


def dense_frames(work: Path, start: float, end: float, *, interval: float = 0.5,
                 max_frames: int = 60, source: Path | None = None) -> dict:
    from .pipeline import session_lock
    with session_lock(work):
        return _dense_frames(work, start, end, interval=interval, max_frames=max_frames, source=source)


def _dense_frames(work: Path, start: float, end: float, *, interval: float,
                  max_frames: int, source: Path | None) -> dict:
    data = load(work)
    valid_span(start, end, data["manifest"]["duration"])
    if not math.isfinite(interval) or interval <= 0 or not 1 <= max_frames <= 120 or end - start > 120:
        raise ValueError("Use a positive interval, a range up to 120 seconds, and at most 120 frames")
    if math.ceil((end - start) / interval) + 1 > max_frames:
        raise ValueError("Requested sampling exceeds max_frames; shorten the range or increase the interval")
    path = source_path(work, data, source)
    import av
    identity = fingerprint({"snapshot": data["snapshot"], "start": start, "end": end, "interval": interval})[:16]
    directory = work / "inspection/frames"
    directory.mkdir(parents=True, exist_ok=True)
    frames = []
    origin = data["manifest"]["source_time_origin"]
    with av.open(str(path)) as container:
        index = data["manifest"].get("video_stream_index")
        stream = next((s for s in container.streams.video if s.index == index), container.streams.video[0])
        container.seek(int((start + origin) * av.time_base), backward=True)
        next_time = start
        for frame in container.decode(stream):
            if frame.pts is None or not frame.time_base:
                continue
            t = float(frame.pts * frame.time_base) - origin
            if t > end + 1e-6:
                break
            if t + 1e-6 < next_time:
                continue
            fid = f"d{identity}-{len(frames):03d}"
            relative = f"inspection/frames/{fid}.jpg"
            image = frame.to_image().convert("RGB")
            image.thumbnail((1280, 1280))
            import os
            temporary = (work / relative).with_suffix(".partial.jpg")
            image.save(temporary, quality=88)
            os.replace(temporary, work / relative)
            frames.append({"id": fid, "time": round(t, 6), "image": relative,
                           "reasons": ["agent-requested-dense-sample"], "snapshot": data["snapshot"]})
            next_time = t + interval
            if len(frames) >= max_frames:
                break
    if not frames:
        raise ValueError("No timestamped video frames in the requested range")
    # This registry belongs to the same source snapshot and makes dense evidence
    # citable without rewriting the baseline visual observations or transcript.
    registry = work / "inspection/evidence.json"
    old = read_json(registry).get("frames", []) if registry.exists() else []
    ids = {f["id"] for f in frames}
    write_json(registry, {"frames": [f for f in old if f["id"] not in ids] + frames})
    return {"snapshot": data["snapshot"], "frames": frames, "start": start, "end": end, "interval": interval}
