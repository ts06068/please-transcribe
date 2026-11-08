from __future__ import annotations

import base64
from html.parser import HTMLParser
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from avtimeline.cli import parser
from avtimeline.export import export, render_subtitles
from avtimeline.review import bundle, import_response, run_command
from avtimeline.storage import asset_path, fingerprint, read_json, timestamp, write_json
from avtimeline.timeline import build
from avtimeline.transcript import normalize, transcribe


def fixture(work: Path) -> dict:
    (work / "frames").mkdir()
    # An owned 1x1 JPEG; core tests need no imaging package to read this fixture.
    (work / "frames/f000000.jpg").write_bytes(base64.b64decode(
        "/9j/4AAQSkZJRgABAQAAAQABAAD/2wBDAAgGBgcGBQgHBwcJCQgKDBQNDAsLDBkSEw8UHRofHh0aHBwgJC4nICIsIxwcKDcpLDAxNDQ0Hyc5PTgyPC4zNDL/"
        "2wBDAQkJCQwLDBgNDRgyIRwhMjIyMjIyMjIyMjIyMjIyMjIyMjIyMjIyMjIyMjIyMjIyMjIyMjIyMjIyMjIyMjIyMjL/wAARCAABAAEDASIAAhEBAxEB/"
        "8QAHwAAAQUBAQEBAQEAAAAAAAAAAAECAwQFBgcICQoL/8QAtRAAAgEDAwIEAwUFBAQAAAF9AQIDAAQRBRIhMUEGE1FhByJxFDKBkaEII0KxwRVS0fAkM2Jy"
        "ggkKFhcYGRolJicoKSo0NTY3ODk6Q0RFRkdISUpTVFVWV1hZWmNkZWZnaGlqc3R1dnd4eXqDhIWGh4iJipKTlJWWl5iZmqKjpKWmp6ipqrKztLW2t7i5"
        "usLDxMXGx8jJytLT1NXW19jZ2uHi4+Tl5ufo6erx8vP09fb3+Pn6/8QAHwEAAwEBAQEBAQEBAQAAAAAAAAECAwQFBgcICQoL/8QAtREAAgECBAQDBAcF"
        "BAQAAQJ3AAECAxEEBSExBhJBUQdhcRMiMoEIFEKRobHBCSMzUvAVYnLRChYkNOEl8RcYGRomJygpKjU2Nzg5OkNERUZHSElKU1RVVldYWVpjZGVmZ2hp"
        "anN0dXZ3eHl6goOEhYaHiImKkpOUlZaXmJmaoqOkpaanqKmqsrO0tba3uLm6wsPExcbHyMnK0tPU1dbX2Nna4uPk5ebn6Onq8vP09fb3+Pn6/9oADAMB"
        "AAIRAxEAPwD3+iiigD//2Q=="))
    manifest = {"duration": 130.0, "source_time_origin": 2.0,
                "source": {"name": "발표 & demo.mp4", "sha256": "fixture"}}
    write_json(work / "manifest.json", manifest)
    raw = {"language": None, "segments": [
        {"start": 0.5, "end": 2.0, "text": " Hello, hello.", "language": "en",
         "words": [{"start": 0.5, "end": 1.2, "word": " Hello,"},
                   {"start": 1.3, "end": 2.0, "word": " hello."}]},
        {"start": 59.5, "end": 61.5, "text": " 이제 화면의 결과를 보세요.", "language": "ko"},
        {"start": 60.0, "end": 63.0, "text": " Aquí vemos el resultado.", "language": "es"},
        {"start": 120.0, "end": 124.0, "text": " 한국어와 English, español <script>alert(1)</script> [x](file:///secret)",
         "language": None},
    ]}
    write_json(work / "transcript.raw.json", normalize(raw, 130, backend="fixture", model="none"))
    write_json(work / "visual.json", {
        "frames": [{"id": "f000000", "time": 60.0, "image": "frames/f000000.jpg", "reasons": ["at-change"]}],
        "events": [{"id": "e000000", "time": 60.0, "observed_between": [59.5, 60.0],
                    "kind": "visual_change_candidate", "frame_ids": ["f000000"]}],
    })
    return build(work)


def response(data: dict) -> dict:
    return {"snapshot": data["snapshot"], "reviewer": "test harness",
            "corrections": [{"segment_id": "s000000", "original_text": " Hello, hello.",
                             "proposed_text": " Hello, hello!", "reason": "Candidate only, needs listening",
                             "evidence_frame_ids": []}],
            "event_annotations": [{"event_id": "e000000", "description": "Result screen appears",
                                   "related_segment_ids": ["s000001", "s000002"],
                                   "evidence_frame_ids": ["f000000"]}],
            "uncertainties": [{"segment_id": "s000003", "reason": "Audio unavailable to this reviewer"}]}


class DocumentParser(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.tags = []
        self.attrs = []
        self.segment_ids = []
        self.images = []
        self.active_segment = None
        self.segment_text = {}
        self.in_p = False
        self.first_p = False

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        self.tags.append(tag)
        self.attrs.extend(attrs)
        if tag == "section":
            self.active_segment = attrs["id"]
            self.segment_ids.append(self.active_segment)
            self.segment_text[self.active_segment] = ""
            self.first_p = True
        if tag == "p" and self.active_segment and self.first_p:
            self.in_p = True
        if tag == "img":
            self.images.append(attrs["src"])

    def handle_endtag(self, tag):
        if tag == "p" and self.in_p:
            self.in_p = False
            self.first_p = False
        if tag == "section":
            self.active_segment = None

    def handle_data(self, text):
        if self.active_segment and self.in_p:
            self.segment_text[self.active_segment] += text


class CoreTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.work = Path(self.temp.name)
        self.data = fixture(self.work)

    def tearDown(self):
        self.temp.cleanup()

    def test_multilingual_text_and_every_word_survive_exports_and_review(self):
        original = (self.work / "transcript.raw.json").read_bytes()
        import_response(self.work, response(self.data))
        output = self.work / "exports"
        export(self.work, output, ["md", "html", "json", "srt", "vtt"])
        self.assertEqual(original, (self.work / "transcript.raw.json").read_bytes())
        document = DocumentParser()
        document.feed((output / "index.html").read_text(encoding="utf-8"))
        segments = self.data["transcript"]["segments"]
        self.assertEqual([s["id"] for s in segments], document.segment_ids)
        self.assertEqual({s["id"]: s["text"] for s in segments}, document.segment_text)
        result = read_json(output / "transcript.json")
        self.assertEqual(self.data["transcript"], result["transcript"])
        self.assertEqual(1, len(result["reviews"]))
        for segment in segments:
            self.assertIn(segment["text"].strip(), (output / "transcript.srt").read_text(encoding="utf-8"))

    def test_html_has_native_components_and_no_styles_or_script(self):
        output = self.work / "exports"
        export(self.work, output, ["html"])
        document = DocumentParser()
        document.feed((output / "index.html").read_text(encoding="utf-8"))
        self.assertFalse({"style", "script", "link"} & set(document.tags))
        self.assertFalse({"style", "class", "onclick"} & set(document.attrs))
        self.assertTrue(document.images[0].startswith("data:image/jpeg;base64,"))
        self.assertIn("<script>alert(1)</script>", document.segment_text["s000003"])

    def test_review_windows_preserve_boundary_segments_exactly_once_as_primary(self):
        request = bundle(self.work, window=60, context=5)
        packets = [read_json(self.work / p) for p in request["packets"]]
        primary = [sid for p in packets for sid in p["primary_segment_ids"]]
        self.assertCountEqual(primary, [s["id"] for s in self.data["transcript"]["segments"]])
        self.assertEqual(len(primary), len(set(primary)))
        self.assertIn("s000001", [s["id"] for s in packets[1]["segments"]])
        self.assertEqual(self.data["snapshot"], request["snapshot"])

    def test_review_rejects_stale_wrong_text_and_unknown_references(self):
        for mutate in [
            lambda r: r.update(snapshot="stale"),
            lambda r: r["corrections"][0].update(original_text="hello"),
            lambda r: r["corrections"][0].update(evidence_frame_ids=["invented"]),
            lambda r: r["event_annotations"][0].update(related_segment_ids=["invented"]),
            lambda r: r.update(unexpected="discard the original transcript"),
        ]:
            with self.subTest(mutation=mutate):
                value = response(self.data)
                mutate(value)
                with self.assertRaises(ValueError):
                    import_response(self.work, value)

    def test_multiple_harness_reviews_coexist_and_stale_reports_stay_off_exports(self):
        first = response(self.data)
        import_response(self.work, first)
        second = response(self.data)
        second["reviewer"] = "another harness"
        import_response(self.work, second)
        self.assertEqual(2, len(list((self.work / "reviews").glob("*.json"))))
        self.data["snapshot"] = "different"
        write_json(self.work / "timeline.json", self.data)
        export(self.work, self.work / "exports", ["json"])
        self.assertEqual([], read_json(self.work / "exports/transcript.json")["reviews"])

    def test_generic_harness_wrapper_uses_json_without_shell(self):
        bundle(self.work)
        wrapper = self.work / "wrapper.py"
        body = ("import json, sys\nrequest = json.load(sys.stdin)\n"
                "print(json.dumps({'snapshot': request['snapshot'], 'reviewer': 'wrapper', "
                "'corrections': [], 'event_annotations': [], 'uncertainties': []}))\n")
        wrapper.write_text(body, encoding="utf-8")
        path = run_command(self.work, [sys.executable, str(wrapper)])
        self.assertEqual("wrapper", read_json(path)["reviewer"])

    def test_imported_asr_is_preserved_and_cannot_overwrite_baseline(self):
        external = self.work / "external.json"
        write_json(external, {"language": "ko", "segments": [{"start": 1, "end": 2, "text": " 안녕하세요."}]})
        kwargs = dict(backend="faster-whisper", model=None, language="auto", multilingual=False,
                      device="cpu", compute_type="int8", import_path=external)
        with self.assertRaises(ValueError):
            transcribe(self.work, **kwargs)
        (self.work / "transcript.raw.json").unlink()
        transcribe(self.work, **kwargs)
        self.assertEqual(" 안녕하세요.", read_json(self.work / "transcript.raw.json")["segments"][0]["text"])

    def test_timestamps_reject_invalid_numbers_and_keep_original_precision(self):
        for start, end in [(-1, 1), (2, 1), (0, 131), (float("nan"), 1), (0, float("inf"))]:
            with self.subTest(start=start, end=end):
                with self.assertRaises(ValueError):
                    normalize({"segments": [{"start": start, "end": end, "text": "x"}]}, 130,
                              backend="test", model="none")
        self.assertEqual("01:00:00.000", timestamp(3599.9999))
        self.assertEqual("00:00:01,250", timestamp(1.25, srt=True))

    def test_markdown_image_bundle_and_markup_escaping(self):
        output = self.work / "exports"
        export(self.work, output, ["md"])
        text = (output / "index.md").read_text(encoding="utf-8")
        self.assertIn("한국어와 English, español", text)
        self.assertIn("&lt;script&gt;", text)
        self.assertNotIn("[x](file:///secret)", text)
        self.assertTrue((output / "frames/f000000.jpg").exists())

    def test_assets_cannot_escape_work_directory(self):
        with self.assertRaises(ValueError):
            asset_path(self.work, "../../secret.jpg")

    def test_cli_validates_options_and_supports_all_requested_formats(self):
        args = parser().parse_args(["run", "demo.mp4", "--work", "session", "--language", "es",
                                    "--formats", "md,html,pdf", "--multilingual"])
        self.assertEqual("es", args.language)
        self.assertTrue(args.multilingual)
        self.assertEqual((10, 20, 300, 200), parser().parse_args([
            "prepare", "demo.mp4", "--work", "session", "--crop", "10,20,300,200"]).crop)

    def test_vtt_escapes_markup_without_changing_source_json(self):
        result = render_subtitles(self.data, vtt=True)
        self.assertTrue(result.startswith("WEBVTT\n"))
        self.assertIn("&lt;script&gt;", result)

    @unittest.skipUnless(os.environ.get("AVTIMELINE_TEST_PDF") == "1", "Optional Chromium PDF check")
    def test_pdf_is_a_real_document(self):
        from PIL import Image
        Image.new("RGB", (20, 20), "red").save(self.work / "frames/f000000.jpg")
        export(self.work, self.work / "exports", ["pdf"])
        pdf = (self.work / "exports/index.pdf").read_bytes()
        self.assertTrue(pdf.startswith(b"%PDF-"))
        self.assertGreater(len(pdf), 1000)


if __name__ == "__main__":
    unittest.main()
