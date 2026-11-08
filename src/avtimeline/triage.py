"""Heuristic review candidates, explicit coverage, and append-only decisions."""
from __future__ import annotations

import base64
import html
from pathlib import Path
import re
import time
import uuid

from .evidence import clip_audio, get_context, load
from .review import load_reviews
from .storage import asset_path, fingerprint, read_json, timestamp, write_json


def candidates(work: Path) -> dict:
    data = load(work)
    reviews = load_reviews(work, data["snapshot"])
    segments = {s["id"]: s for s in data["transcript"]["segments"]}
    found = []

    def add(kind, start, end, reason, segment_ids, priority=1, **extra):
        item = {"kind": kind, "start": start, "end": end, "reason": reason,
                "segment_ids": segment_ids, "priority": priority, **extra}
        item["id"] = "c" + fingerprint({"snapshot": data["snapshot"], **item})[:20]
        found.append(item)

    audit = data.get("audit")
    if audit:
        for gap in audit["candidates"]:
            ids = [s["id"] for s in segments.values() if s["start"] <= gap["end"] and s["end"] >= gap["start"]]
            add("possible_missing_speech", gap["start"], gap["end"], "VAD speech without ASR time coverage", ids, 3)
    previous_text, repetition = None, 0
    for segment in segments.values():
        text = segment["text"].strip()
        score = segment.get("avg_logprob")
        if score is not None and score < -1:
            add("low_asr_score", segment["start"], segment["end"],
                "ASR average log probability below -1; heuristic, not a calibrated confidence", [segment["id"]], 2)
        low = [w for w in segment.get("words", []) if w.get("probability") is not None and w["probability"] < 0.6]
        if low:
            add("low_word_score", min(w["start"] for w in low), max(w["end"] for w in low),
                "Word probability below 0.6; inspect audio before changing text", [segment["id"]], 2,
                word_ids=[w["id"] for w in low])
        if not text and segment["end"] > segment["start"]:
            add("empty_asr_segment", segment["start"], segment["end"], "Timestamped segment has no text", [segment["id"]], 2)
        normalized = re.sub(r"\W+", "", text.casefold())
        repetition = repetition + 1 if normalized and normalized == previous_text else 1
        if repetition >= 3:
            add("repeated_text", segment["start"], segment["end"],
                "At least three adjacent identical segments; repetition may be genuine speech", [segment["id"]])
        previous_text = normalized
    for report in reviews:
        report_id = fingerprint(report)
        for index, correction in enumerate(report["corrections"]):
            segment = segments[correction["segment_id"]]
            proposal_id = "p" + fingerprint({"report": report_id, "index": index})[:20]
            add("correction_proposal", segment["start"], segment["end"], correction["reason"], [segment["id"]], 2,
                proposal_id=proposal_id, original_text=correction["original_text"],
                proposed_text=correction["proposed_text"], reviewer=report["reviewer"])
        for item in report["uncertainties"]:
            segment = segments[item["segment_id"]]
            add("reviewer_uncertainty", segment["start"], segment["end"], item["reason"], [segment["id"]], 2,
                reviewer=report["reviewer"])
    # Collapse repeated reports of the exact same candidate, preserving IDs.
    unique = {item["id"]: item for item in found}
    result = sorted(unique.values(), key=lambda c: (-c["priority"], c["start"], c["id"]))
    return {"snapshot": data["snapshot"], "audit_performed": audit is not None, "candidates": result,
            "note": "Heuristic candidates only. No candidates does not prove accurate or complete transcription."}


def decisions(work: Path, snapshot: str) -> list[dict]:
    return [item for path in sorted((work / "decisions").glob("*.json"))
            if (item := read_json(path))["snapshot"] == snapshot]


def decide(work: Path, candidate_id: str, decision: str, actor: str, reason: str,
           *, audio_inspected: bool = False) -> dict:
    if type(audio_inspected) is not bool:
        raise ValueError("Audio inspection must be an explicit boolean attestation")
    if decision not in ("accepted", "rejected", "deferred") or not actor.strip() or not reason.strip():
        raise ValueError("Choose accepted/rejected/deferred and provide actor and reason")
    queue = candidates(work)
    candidate = next((c for c in queue["candidates"] if c["id"] == candidate_id), None)
    if candidate is None:
        raise ValueError("Candidate does not exist in the current snapshot")
    if decision == "accepted" and candidate["kind"] == "correction_proposal" and not audio_inspected:
        raise ValueError("Accepting a speech correction requires audio inspection; otherwise defer it")
    item = {"id": uuid.uuid4().hex, "snapshot": queue["snapshot"], "candidate_id": candidate_id,
            "proposal_id": candidate.get("proposal_id"), "decision": decision, "actor": actor,
            "reason": reason, "audio_inspected": audio_inspected, "created": time.time()}
    write_json(work / "decisions" / (item["id"] + ".json"), item, exclusive=True)
    return item


def mark_coverage(work: Path, segment_ids: list[str], reviewer: str, *, images_inspected: bool,
                  audio_inspected: bool) -> dict:
    if type(images_inspected) is not bool or type(audio_inspected) is not bool:
        raise ValueError("Inspection flags must be explicit booleans")
    data = load(work)
    valid = {s["id"] for s in data["transcript"]["segments"]}
    if not reviewer.strip() or not segment_ids or set(segment_ids) - valid:
        raise ValueError("Supply a reviewer and existing segment IDs")
    item = {"snapshot": data["snapshot"], "reviewer": reviewer, "segment_ids": list(dict.fromkeys(segment_ids)),
            "images_inspected": bool(images_inspected), "audio_inspected": bool(audio_inspected)}
    write_json(work / "coverage" / (fingerprint(item) + ".json"), item)
    return item


def status(work: Path) -> dict:
    data = load(work)
    all_ids = [s["id"] for s in data["transcript"]["segments"]]
    records = [item for path in sorted((work / "coverage").glob("*.json"))
               if (item := read_json(path))["snapshot"] == data["snapshot"]]
    reviewed, audio_reviewed, image_reviewed = set(), set(), set()
    for record in records:
        reviewed.update(record["segment_ids"])
        if record["audio_inspected"]:
            audio_reviewed.update(record["segment_ids"])
        if record["images_inspected"]:
            image_reviewed.update(record["segment_ids"])
    queue = candidates(work)
    latest = {}
    for item in sorted(decisions(work, data["snapshot"]), key=lambda d: d["created"]):
        latest[item["candidate_id"]] = item
    return {"snapshot": data["snapshot"], "segments_total": len(all_ids), "reviewed": len(reviewed),
            "audio_reviewed": len(audio_reviewed), "images_reviewed": len(image_reviewed),
            "unreviewed_segment_ids": [sid for sid in all_ids if sid not in reviewed],
            "pending_candidate_ids": [c["id"] for c in queue["candidates"]
                if latest.get(c["id"], {}).get("decision") not in ("accepted", "rejected")],
            "candidates_total": len(queue["candidates"]), "decisions": list(latest.values()),
            "note": "Coverage records are reviewer attestations, not proof of ASR correctness."}


def candidate_context(work: Path, candidate_id: str, *, padding: float = 3) -> dict:
    queue = candidates(work)
    candidate = next((item for item in queue["candidates"] if item["id"] == candidate_id), None)
    if candidate is None:
        raise ValueError("Unknown candidate")
    data = load(work)
    start = max(0, candidate["start"] - padding)
    end = min(data["manifest"]["duration"], candidate["end"] + padding)
    # Long low-score segments are investigated in bounded pieces.
    end = min(end, start + 120)
    return {"candidate": candidate, "context": get_context(work, start, end),
            "audio": clip_audio(work, start, end), "range_truncated": end < candidate["end"] + padding}


def focus(work: Path, *, limit: int = 20, offset: int = 0) -> dict:
    if not 1 <= limit <= 100 or offset < 0:
        raise ValueError("Use a limit from 1 to 100 and nonnegative offset")
    queue = candidates(work)
    selected = queue["candidates"][offset:offset + limit]
    chunks = ['<!doctype html><html lang="und"><head><meta charset="utf-8"><title>Review queue</title></head><body>',
              '<h1>Review queue</h1>', '<p>Heuristic candidates. Inspect the audio and screen evidence before deciding.</p>',
              f'<p>{offset + 1 if selected else 0}–{offset + len(selected)} of {len(queue["candidates"])} candidates.</p>']
    if not queue["audit_performed"]:
        chunks.append('<p>No VAD coverage audit is present. Missing speech has not been checked by VAD.</p>')
    items = []
    for candidate in selected:
        item = candidate_context(work, candidate["id"])
        items.append(item)
        chunks += [f'<section id="{candidate["id"]}"><h2>{timestamp(candidate["start"])}–{timestamp(candidate["end"])}</h2>',
                   f'<p>{html.escape(candidate["kind"])}: {html.escape(candidate["reason"])}</p>']
        for segment in item["context"]["segments"]:
            chunks.append(f'<p dir="auto">{html.escape(segment["text"])}</p>')
        if "proposed_text" in candidate:
            chunks.append(f'<p>Proposed: {html.escape(candidate["proposed_text"])}</p>')
        if item["audio"]["available"]:
            audio = base64.b64encode(asset_path(work, item["audio"]["audio"]).read_bytes()).decode()
            chunks.append(f'<p>Audio excerpt: {timestamp(item["audio"]["start"])}–{timestamp(item["audio"]["end"])}.</p>')
            chunks.append(f'<audio controls src="data:audio/wav;base64,{audio}">Audio excerpt</audio>')
        for frame in item["context"]["frames"]:
            image = base64.b64encode(asset_path(work, frame["image"]).read_bytes()).decode()
            chunks.append(f'<figure><img width="600" src="data:image/jpeg;base64,{image}" alt="Screen evidence">'
                          f'<figcaption>{timestamp(frame["time"])} · {html.escape(frame["id"])}</figcaption></figure>')
        chunks.append('</section>')
    chunks.append('</body></html>')
    path = work / "inspection" / f"queue-{offset:06d}.html"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(chunks), encoding="utf-8")
    result = {"snapshot": queue["snapshot"], "total": len(queue["candidates"]), "items": items,
              "next_offset": offset + limit if offset + limit < len(queue["candidates"]) else None,
              "html": str(path.relative_to(work))}
    write_json(path.with_suffix(".json"), result)
    return result
