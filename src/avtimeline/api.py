"""Stable Python entry points shared with the CLI and optional MCP server.

All times are seconds on the session's normalized source timeline. The functions
read existing sessions; only explicit extraction/review calls create new evidence.
"""
from .evidence import get_context, clip_audio, dense_frames
from .triage import candidates, candidate_context, mark_coverage, status as review_status, decide
from .review import import_response as submit_review

__all__ = ["get_context", "clip_audio", "dense_frames", "candidates", "candidate_context",
           "mark_coverage", "review_status", "decide", "submit_review"]
