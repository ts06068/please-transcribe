from __future__ import annotations

import os
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from avtimeline.annotations import annotations, speech_html, PDF_LINK_PREFIX
from avtimeline.export import export
from avtimeline.review import import_response
from avtimeline.storage import read_json
from avtimeline.triage import candidates, decide
from test_core import fixture


class AnnotationTests(unittest.TestCase):
    def test_changed_phrase_has_exact_raw_tooltip_without_rewriting_visible_text(self):
        segment = {"id": "s0", "text": 'A <correct> "word".', "original_text": 'A <wrong> "word".'}
        marks = annotations({"transcript": {"segments": [segment]}}, [])
        self.assertEqual("<wrong>", marks[0]["raw_text"])
        rendered = speech_html(segment, marks)
        self.assertIn('title="Raw text: &lt;wrong&gt;"', rendered)
        self.assertIn('&lt;correct&gt;</mark>', rendered)
        self.assertNotIn('style=', rendered)
        self.assertNotIn('<script', rendered)

    def test_uncertain_unchanged_speech_and_insertions_remain_traceable(self):
        segment = {"id": "s0", "text": "안녕하세요.\nHola."}
        marks = annotations({"transcript": {"segments": [segment]}}, [{"segment_ids": ["s0"]}])
        self.assertEqual(segment["text"], marks[0]["raw_text"])
        self.assertIn('title="Raw text: 안녕하세요.&#10;Hola."', speech_html(segment, marks))
        inserted = {"id": "s1", "text": "one new word", "original_text": "one word"}
        marks = annotations({"transcript": {"segments": [inserted]}}, [])
        self.assertEqual("", marks[0]["raw_text"])
        self.assertIn("no corresponding text", speech_html(inserted, marks))

    @unittest.skipUnless(os.environ.get("AVTIMELINE_TEST_PDF") == "1", "Requires Chromium and PDF extras")
    def test_real_pdf_has_yellow_highlights_unicode_comments_and_precise_text(self):
        import pymupdf
        with tempfile.TemporaryDirectory() as folder:
            work = Path(folder)
            data = fixture(work)
            edits = [("s000000", " Hello, hello!"),
                     ("s000001", " 이제 화면의 결과를 확인하세요."),
                     ("s000002", " Aquí mostramos el resultado.")]
            report = {"snapshot": data["snapshot"], "reviewer": "Synthetic PDF test",
                      "corrections": [{"segment_id": sid,
                          "original_text": next(s["text"] for s in data["transcript"]["segments"] if s["id"] == sid),
                          "proposed_text": text, "reason": "Controlled fixture", "evidence_frame_ids": []}
                          for sid, text in edits], "event_annotations": [], "uncertainties": []}
            import_response(work, report)
            for candidate in candidates(work)["candidates"]:
                decide(work, candidate["id"], "accepted", "Synthetic PDF test", "Fixture attestation", audio_inspected=True)
            export(work, work / "exports", ["html", "pdf", "json"], text_version="polished")
            expected = read_json(work / "exports/polished/transcript.json")["raw_annotations"]
            self.assertEqual(3, len(expected))
            comments, visible = set(), []
            with pymupdf.open(work / "exports/polished/index.pdf") as pdf:
                for page in pdf:
                    self.assertFalse(any(link.get("uri", "").startswith(PDF_LINK_PREFIX) for link in page.get_links()))
                    for mark in page.annots() or []:
                        self.assertEqual(pymupdf.PDF_ANNOT_HIGHLIGHT, mark.type[0])
                        self.assertEqual((1.0, 1.0, 0.0), tuple(mark.colors["stroke"]))
                        self.assertTrue(mark.has_popup)
                        comments.add(mark.info["content"])
                        visible.append(page.get_textbox(mark.rect))
                        pix = page.get_pixmap(clip=mark.rect, alpha=False)
                        pixels = pix.samples
                        self.assertTrue(any(pixels[i] > 200 and pixels[i + 1] > 200 and pixels[i + 2] < 180
                                            for i in range(0, len(pixels) - 2, pix.n)))
            self.assertEqual({"Raw text: " + item["raw_text"] for item in expected}, comments)
            self.assertTrue(any("hello!" in text for text in visible))
            self.assertIn('title="Raw text: 보세요."', (work / "exports/polished/index.html").read_text(encoding="utf-8"))

    @unittest.skipUnless(os.environ.get("AVTIMELINE_TEST_PDF") == "1", "Requires Chromium and PDF extras")
    def test_one_uncertain_passage_can_wrap_across_multiple_pdf_pages(self):
        import pymupdf
        from avtimeline.storage import write_json
        from avtimeline.timeline import build
        with tempfile.TemporaryDirectory() as folder:
            work = Path(folder)
            fixture(work)
            raw = read_json(work / "transcript.raw.json")
            passage = "A repeated phrase stays tied to this segment. " * 600
            raw["segments"][0].update(text=passage, words=[], avg_logprob=-2)
            write_json(work / "transcript.raw.json", raw)
            build(work)
            export(work, work / "exports", ["pdf"])
            pages = []
            with pymupdf.open(work / "exports/index.pdf") as pdf:
                for index, page in enumerate(pdf):
                    for mark in page.annots() or []:
                        self.assertEqual("Raw text: " + passage, mark.info["content"])
                        pages.append(index)
            self.assertGreater(len(set(pages)), 1)


if __name__ == "__main__":
    unittest.main()
