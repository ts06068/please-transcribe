from __future__ import annotations

from bisect import bisect_left, bisect_right
from pathlib import Path

from .storage import SCHEMA_VERSION, fingerprint, read_json, write_json


def build(work: Path, *, context: float = 5.0) -> dict:
    manifest = read_json(work / "manifest.json")
    transcript = read_json(work / "transcript.raw.json")
    visual = read_json(work / "visual.json")
    frames = sorted(visual["frames"], key=lambda item: item["time"])
    frame_times = [f["time"] for f in frames]
    events = sorted(visual["events"], key=lambda item: item["time"])
    event_times = [e["time"] for e in events]
    links = []
    for segment in transcript["segments"]:
        start, end = segment["start"], segment["end"]
        left = bisect_left(frame_times, max(0, start - context))
        right = bisect_right(frame_times, end + context)
        # Include a preceding observation when the scene has been stable for a while.
        if left > 0:
            left -= 1
        event_left = bisect_left(event_times, max(0, start - context))
        event_right = bisect_right(event_times, end + context)
        links.append({"segment_id": segment["id"],
                      "frame_ids": [f["id"] for f in frames[left:right]],
                      "event_ids": [e["id"] for e in events[event_left:event_right]],
                      "relationship": "temporal_context_only"})
    data = {"schema_version": SCHEMA_VERSION, "manifest": manifest,
            "transcript": transcript, "visual": visual, "links": links,
            "audit": read_json(work / "audit.json") if (work / "audit.json").exists() else None,
            "context_seconds": context}
    data["snapshot"] = fingerprint(data)
    write_json(work / "timeline.json", data)
    return data
