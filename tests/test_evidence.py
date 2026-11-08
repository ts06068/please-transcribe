from __future__ import annotations

import asyncio
import importlib.util
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch
import uuid
import wave

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from test_core import fixture, response
from avtimeline.evidence import clip_audio, get_context
from avtimeline.export import export
from avtimeline.review import import_response
from avtimeline.storage import read_json, write_json
from avtimeline.timeline import build
from avtimeline.triage import candidates, decide, focus, mark_coverage, status


class EvidenceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.work = Path(self.temp.name)
        self.data = fixture(self.work)
        audio = bytearray(130 * 16000 * 2)
        audio[32000:32002] = b"\xff\x7f"  # Exact pulse at 1.0 seconds.
        with wave.open(str(self.work / "audio.wav"), "wb") as wav:
            wav.setparams((1, 2, 16000, 0, "NONE", "not compressed"))
            wav.writeframes(audio)

    def tearDown(self):
        self.temp.cleanup()

    def test_clip_retains_exact_source_coordinates_and_samples(self):
        result = clip_audio(self.work, 0.75, 1.25)
        self.assertEqual((0.75, 1.25), (result["start"], result["end"]))
        with wave.open(str(self.work / result["audio"])) as wav:
            self.assertEqual(8000, wav.getnframes())
            audio = wav.readframes(wav.getnframes())
        self.assertEqual(b"\xff\x7f", audio[8000:8002])

    def test_context_preserves_boundary_speech_and_reports_pagination(self):
        result = get_context(self.work, 59, 64, context=0, max_segments=1)
        self.assertEqual(2, result["segment_total"])
        self.assertEqual(1, result["next_segment_offset"])
        second = get_context(self.work, 59, 64, context=0, max_segments=1, offset=1)
        self.assertEqual("es", second["segments"][0]["language"])
        self.assertEqual("temporal_context_only", self.data["links"][0]["relationship"])
        with self.assertRaises(ValueError):
            get_context(self.work, -1, 3)
        with self.assertRaises(ValueError):
            clip_audio(self.work, 0, 130)

    def test_changed_baseline_requires_rebuild(self):
        raw = read_json(self.work / "transcript.raw.json")
        raw["segments"][0]["text"] = "Edited source"
        write_json(self.work / "transcript.raw.json", raw)
        with self.assertRaisesRegex(ValueError, "changed"):
            get_context(self.work, 0, 2)

    def test_queue_combines_vad_low_score_and_proposals_without_rewriting(self):
        raw = read_json(self.work / "transcript.raw.json")
        raw["segments"][0]["avg_logprob"] = -2
        raw["segments"][0]["words"][0]["probability"] = 0.1
        write_json(self.work / "transcript.raw.json", raw)
        write_json(self.work / "audit.json", {"candidates": [
            {"start": 8, "end": 9, "kind": "possible_missing_speech"}]})
        data = build(self.work)
        original = (self.work / "transcript.raw.json").read_bytes()
        import_response(self.work, response(data))
        queue = candidates(self.work)
        self.assertTrue(queue["audit_performed"])
        self.assertTrue({"possible_missing_speech", "low_asr_score", "low_word_score", "correction_proposal"}
                        <= {c["kind"] for c in queue["candidates"]})
        page = focus(self.work, limit=2)
        html = (self.work / page["html"]).read_text(encoding="utf-8")
        self.assertIn('<audio controls', html)
        self.assertNotIn('<style', html)
        self.assertNotIn('<script', html)
        self.assertIsNotNone(page["next_offset"])
        self.assertEqual(original, (self.work / "transcript.raw.json").read_bytes())

    def test_decisions_and_coverage_are_explicit_and_retained_in_exports(self):
        import_response(self.work, response(self.data))
        candidate = next(c for c in candidates(self.work)["candidates"] if c["kind"] == "correction_proposal")
        with self.assertRaisesRegex(ValueError, "audio inspection"):
            decide(self.work, candidate["id"], "accepted", "reviewer", "Screen terminology")
        decide(self.work, candidate["id"], "deferred", "reviewer", "Audio inspection is unavailable")
        mark_coverage(self.work, ["s000000"], "reviewer", images_inspected=True, audio_inspected=False)
        report = status(self.work)
        self.assertEqual(1, report["reviewed"])
        self.assertEqual(0, report["audio_reviewed"])
        self.assertIn("s000001", report["unreviewed_segment_ids"])
        export(self.work, self.work / "exports", ["html", "json"])
        document = (self.work / "exports/index.html").read_text(encoding="utf-8")
        self.assertIn("Decision: deferred", document)
        self.assertIn("1 / 4 speech segments", document)
        self.assertEqual(" Hello, hello.", read_json(self.work / "exports/transcript.json")["transcript"]["segments"][0]["text"])
        self.assertEqual(1, len(read_json(self.work / "exports/transcript.json")["decisions"]))

    def test_decision_history_keeps_acceptance_separate_from_baseline(self):
        original = (self.work / "transcript.raw.json").read_bytes()
        import_response(self.work, response(self.data))
        candidate = next(c for c in candidates(self.work)["candidates"] if c["kind"] == "correction_proposal")
        # Windows can give two writes the same wall-clock timestamp. UUID file
        # order must not make the earlier rejection replace the later acceptance.
        with patch("avtimeline.triage.time.time", return_value=1000), \
                patch("avtimeline.triage.uuid.uuid4", side_effect=[uuid.UUID(int=2), uuid.UUID(int=1)]):
            decide(self.work, candidate["id"], "rejected", "first reviewer", "Initial reading")
            decide(self.work, candidate["id"], "accepted", "second reviewer", "Fixture audio checked", audio_inspected=True)
        export(self.work, self.work / "exports", ["html", "md", "json"])
        output = read_json(self.work / "exports/transcript.json")
        self.assertEqual(2, len(output["decisions"]))
        self.assertIn("Decision: accepted", (self.work / "exports/index.html").read_text(encoding="utf-8"))
        self.assertIn("Decision: accepted", (self.work / "exports/index.md").read_text(encoding="utf-8"))
        self.assertEqual(["rejected", "accepted"], [d["decision"] for d in output["decisions"]])
        self.assertEqual("accepted", status(self.work)["decisions"][0]["decision"])
        # A clock adjustment must not undo a subsequent decision either.
        with patch("avtimeline.triage.time.time", return_value=999):
            decide(self.work, candidate["id"], "deferred", "third reviewer", "Recheck requested")
        export(self.work, self.work / "exports", ["html", "md", "json"])
        self.assertEqual("deferred", status(self.work)["decisions"][0]["decision"])
        for name in ("index.html", "index.md"):
            self.assertIn("Decision: deferred", (self.work / "exports" / name).read_text(encoding="utf-8"))
        self.assertEqual(3, len(read_json(self.work / "exports/transcript.json")["decisions"]))
        self.assertEqual(original, (self.work / "transcript.raw.json").read_bytes())

    @unittest.skipUnless(importlib.util.find_spec("mcp"), "Optional MCP SDK")
    def test_real_mcp_stdio_tools_and_root_restriction(self):
        from mcp import ClientSession, StdioServerParameters
        from mcp.client.stdio import stdio_client

        async def check():
            params = StdioServerParameters(command=sys.executable,
                args=["-m", "avtimeline", "mcp", "--root", str(self.work.parent)],
                env={"PYTHONPATH": str(Path(__file__).resolve().parents[1] / "src")})
            async with stdio_client(params) as streams:
                async with ClientSession(*streams) as client:
                    await client.initialize()
                    tools = await client.list_tools()
                    names = {tool.name for tool in tools.tools}
                    self.assertTrue({"get_context", "get_screen_image", "get_audio_excerpt", "extract_dense_frames",
                                     "submit_review", "get_review_status", "decide_candidate"} <= names)
                    result = await client.call_tool("get_context", {"session_name": self.work.name, "start": 59, "end": 64})
                    self.assertFalse(result.isError)
                    self.assertIn("Aquí", str(result.content))
                    image = await client.call_tool("get_screen_image", {"session_name": self.work.name, "frame_id": "f000000"})
                    self.assertFalse(image.isError)
                    self.assertTrue(any(item.type == "image" for item in image.content))
                    audio = await client.call_tool("get_audio_excerpt", {"session_name": self.work.name, "start": 0.75, "end": 1.25})
                    self.assertFalse(audio.isError)
                    self.assertTrue(any(item.type == "audio" for item in audio.content))
                    from avtimeline.storage import fingerprint
                    review = await client.call_tool("submit_review", {"session_name": self.work.name, "response": response(self.data)})
                    self.assertFalse(review.isError)
                    coverage = await client.call_tool("mark_reviewed", {"session_name": self.work.name,
                        "segment_ids": ["s000000"], "reviewer": "SDK test", "images_inspected": True})
                    self.assertFalse(coverage.isError)
                    report = await client.call_tool("get_review_status", {"session_name": self.work.name})
                    self.assertFalse(report.isError)
                    proposal = next(c for c in candidates(self.work)["candidates"] if c["kind"] == "correction_proposal")
                    decision = await client.call_tool("decide_candidate", {"session_name": self.work.name,
                        "candidate_id": proposal["id"], "decision": "deferred", "actor": "SDK test", "reason": "Audio not inspected"})
                    self.assertFalse(decision.isError)
                    outside = await client.call_tool("get_context", {"session_name": "../../outside", "start": 0, "end": 1})
                    self.assertTrue(outside.isError)

        asyncio.run(check())


if __name__ == "__main__":
    unittest.main()
