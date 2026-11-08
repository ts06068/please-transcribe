"""Optional stdio MCP interface; sessions are scoped to an explicit root."""
from __future__ import annotations

import base64
import json
from pathlib import Path


def create_server(root: Path):
    from mcp.server.fastmcp import FastMCP, Image
    from mcp.types import AudioContent, TextContent
    from . import api
    from .evidence import load, inspection_frames
    from .storage import asset_path

    root = root.expanduser().resolve()
    server = FastMCP("please-transcribe")

    def session(name: str) -> Path:
        path = asset_path(root, name)
        if not (path / "timeline.json").is_file():
            raise ValueError("Choose a prepared session inside the configured root")
        return path

    @server.tool()
    def get_context(session_name: str, start: float, end: float, context: float = 3,
                    max_frames: int = 12, max_segments: int = 100, offset: int = 0) -> dict:
        """Read speech, visual events, and screen references for a bounded time range.

        Truncation and pagination are explicit. Frame references alone are not
        image inspection; use get_screen_image for the actual pixels.
        """
        return api.get_context(session(session_name), start, end, context=context,
                               max_frames=max_frames, max_segments=max_segments, offset=offset)

    @server.tool()
    def get_screen_image(session_name: str, frame_id: str):
        """Return an actual extracted frame, including registered dense evidence."""
        work = session(session_name)
        data = load(work)
        frames = data["visual"]["frames"] + inspection_frames(work, data["snapshot"])
        frame = next((f for f in frames if f["id"] == frame_id), None)
        if frame is None:
            raise ValueError("Unknown frame ID")
        return Image(data=asset_path(work, frame["image"]).read_bytes(), format="jpeg")

    @server.tool()
    def get_audio_excerpt(session_name: str, start: float, end: float) -> list:
        """Return bounded WAV audio plus its exact timeline coordinates.

        Audio support depends on the client. If unsupported, mark uncertainty;
        do not claim to have heard the excerpt merely because it was returned.
        """
        work = session(session_name)
        result = api.clip_audio(work, start, end)
        content = [TextContent(type="text", text=json.dumps(result))]
        if result["available"]:
            content.append(AudioContent(type="audio", mimeType="audio/wav",
                data=base64.b64encode(asset_path(work, result["audio"]).read_bytes()).decode()))
        return content

    @server.tool()
    def extract_dense_frames(session_name: str, start: float, end: float,
                             interval: float = 0.5, max_frames: int = 60) -> dict:
        """Inspect a short interval more densely and register citable image IDs.

        Requires the original video still present at the recorded source path.
        """
        return api.dense_frames(session(session_name), start, end, interval=interval, max_frames=max_frames)

    @server.tool()
    def get_review_status(session_name: str) -> dict:
        """Return explicit review coverage and unresolved candidate IDs."""
        return api.review_status(session(session_name))

    @server.tool()
    def get_review_candidates(session_name: str, offset: int = 0, limit: int = 20) -> dict:
        """List heuristic missing-speech, confidence, repetition, and reviewer flags."""
        if offset < 0 or not 1 <= limit <= 100:
            raise ValueError("Invalid pagination")
        queue = api.candidates(session(session_name))
        items = queue.pop("candidates")
        return {**queue, "total": len(items), "candidates": items[offset:offset + limit],
                "next_offset": offset + limit if offset + limit < len(items) else None}

    @server.tool()
    def get_candidate_context(session_name: str, candidate_id: str) -> dict:
        """Locate the audio excerpt and screen evidence for one review candidate."""
        return api.candidate_context(session(session_name), candidate_id)

    @server.tool()
    def submit_review(session_name: str, response: dict) -> dict:
        """Validate and save proposals using review/response.schema.json.

        Preserve exact original_text and snapshot; never add unspoken slide text.
        """
        path = api.submit_review(session(session_name), response)
        return {"report": path.name, "state": "imported"}

    @server.tool()
    def mark_reviewed(session_name: str, segment_ids: list[str], reviewer: str,
                      images_inspected: bool = False, audio_inspected: bool = False) -> dict:
        """Attest only segments actually reviewed, with truthful modality flags."""
        return api.mark_coverage(session(session_name), segment_ids, reviewer,
                                 images_inspected=images_inspected, audio_inspected=audio_inspected)

    @server.tool()
    def decide_candidate(session_name: str, candidate_id: str, decision: str, actor: str,
                         reason: str, audio_inspected: bool = False) -> dict:
        """Append an accepted/rejected/deferred decision; never overwrite ASR.

        Speech corrections can be accepted only after actual audio inspection.
        """
        return api.decide(session(session_name), candidate_id, decision, actor, reason,
                           audio_inspected=audio_inspected)

    return server


def serve(root: Path):
    create_server(root).run(transport="stdio")
