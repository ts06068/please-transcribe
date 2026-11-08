from __future__ import annotations

import json
from pathlib import Path
import sys
import tempfile
import unittest
import wave

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from test_core import DocumentParser, fixture, response
from avtimeline.export import export
from avtimeline.polish import prepare_polish, export_polished
from avtimeline.review import import_response, run_command
from avtimeline.storage import read_json
from avtimeline.triage import candidates, decide


class PolishTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.work = Path(self.temp.name)
        self.data = fixture(self.work)
        with wave.open(str(self.work / "audio.wav"), "wb") as wav:
            wav.setparams((1, 2, 16000, 0, "NONE", "not compressed"))
            wav.writeframes(b"\0\0" * 130 * 16000)
        self.baseline = (self.work / "transcript.raw.json").read_bytes()
        self.timeline = (self.work / "timeline.json").read_bytes()
        import_response(self.work, response(self.data))

    def tearDown(self):
        self.temp.cleanup()

    def proposals(self):
        return [c for c in candidates(self.work)["candidates"] if c["kind"] == "correction_proposal"]

    def test_focused_request_can_be_reviewed_by_an_existing_harness(self):
        request = prepare_polish(self.work, limit=1)
        self.assertEqual(2, request["total"])
        self.assertEqual(1, request["next_offset"])
        packet = read_json(self.work / request["packets"][0])
        self.assertTrue((self.work / packet["audio"]["audio"]).is_file())
        self.assertEqual(self.data["snapshot"], request["snapshot"])
        script = self.work / "agent.py"
        script.write_text("import json,sys\nr=json.load(sys.stdin)\n"
                          "assert r['task']=='polish-uncertain-speech'\n"
                          "assert json.load(open(r['packets'][0],encoding='utf-8'))['audio']['available']\n"
                          "json.dump(dict(snapshot=r['snapshot'],reviewer='fixture agent',corrections=[],"
                          "event_annotations=[],uncertainties=[]),sys.stdout)\n", encoding="utf-8")
        report = run_command(self.work, [sys.executable, str(script)], request_path=request["request"])
        self.assertEqual("fixture agent", read_json(report)["reviewer"])
        self.assertIsNone(prepare_polish(self.work, offset=1, limit=1)["next_offset"])
        self.assertEqual(self.baseline, (self.work / "transcript.raw.json").read_bytes())

    def test_accepted_correction_exports_separately_without_false_word_alignment(self):
        export(self.work, self.work / "exports", ["json", "html", "srt"])
        original_exports = {p.name: p.read_bytes() for p in (self.work / "exports").iterdir() if p.is_file()}
        candidate = self.proposals()[0]
        with self.assertRaisesRegex(ValueError, "audio inspection"):
            decide(self.work, candidate["id"], "accepted", "fixture agent", "Test")
        decide(self.work, candidate["id"], "accepted", "fixture agent", "Synthetic test attestation", audio_inspected=True)
        result = export_polished(self.work)
        self.assertEqual(5, len(result["files"]))
        data = read_json(self.work / "exports/polished/transcript.json")
        self.assertEqual(" Hello, hello!", data["transcript"]["segments"][0]["text"])
        self.assertEqual(" Hello, hello.", data["original_transcript"]["segments"][0]["text"])
        self.assertTrue(data["original_transcript"]["segments"][0]["words"])
        self.assertEqual([], data["transcript"]["segments"][0]["words"])
        self.assertEqual(1, data["polish"]["changed_segments"])
        self.assertEqual(1, data["polish"]["unresolved_candidates"])
        page = DocumentParser()
        page.feed((self.work / "exports/polished/index.html").read_text(encoding="utf-8"))
        self.assertEqual([s["id"] for s in self.data["transcript"]["segments"]], page.segment_ids)
        self.assertNotIn("style", page.tags)
        self.assertNotIn("script", page.tags)
        for ext in ("srt", "vtt"):
            self.assertIn("Hello, hello!", (self.work / f"exports/polished/transcript.{ext}").read_text(encoding="utf-8"))
        for name, content in original_exports.items():
            self.assertEqual(content, (self.work / "exports" / name).read_bytes())
        self.assertEqual(self.baseline, (self.work / "transcript.raw.json").read_bytes())
        self.assertEqual(self.timeline, (self.work / "timeline.json").read_bytes())
        self.assertEqual(self.data["transcript"]["segments"][1:], data["transcript"]["segments"][1:])

    def test_deferred_or_unaccepted_proposals_do_not_change_speech(self):
        for decision in (None, "deferred", "rejected"):
            if decision:
                decide(self.work, self.proposals()[0]["id"], decision, "fixture agent", "Not verified")
            export_polished(self.work, ["json"])
            data = read_json(self.work / "exports/polished/transcript.json")
            self.assertEqual(0, data["polish"]["changed_segments"])
            self.assertEqual(self.data["transcript"]["segments"], data["transcript"]["segments"])

    def test_conflicting_accepted_proposals_require_resolution(self):
        other = response(self.data)
        other["reviewer"] = "another fixture agent"
        other["corrections"][0]["proposed_text"] = " Hello, hello?"
        import_response(self.work, other)
        proposals = self.proposals()
        for proposal in proposals:
            decide(self.work, proposal["id"], "accepted", "fixture agent", "Synthetic test attestation", audio_inspected=True)
        with self.assertRaisesRegex(ValueError, "Conflicting"):
            export_polished(self.work, ["json"])
        decide(self.work, proposals[0]["id"], "rejected", "fixture agent", "Resolve conflicting proposal")
        export_polished(self.work, ["json"])
        self.assertEqual(proposals[1]["proposed_text"],
                         read_json(self.work / "exports/polished/transcript.json")["transcript"]["segments"][0]["text"])


if __name__ == "__main__":
    unittest.main()
