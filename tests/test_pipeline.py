from __future__ import annotations

import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch
import wave

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from avtimeline.cli import parser
from avtimeline.pipeline import run, batch, session_lock
from avtimeline.progress import Progress
from avtimeline.storage import file_hash, read_json, write_json


class PipelineTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.source = self.root / "source.mp4"
        self.source.write_bytes(b"owned recording fixture")
        self.work = self.root / "session"
        self.args = parser().parse_args(["run", str(self.source), "--work", str(self.work),
            "--backend", "faster-whisper", "--language", "ko", "--formats", "md,html,json", "--progress", "quiet"])

    def tearDown(self):
        self.temp.cleanup()

    def prep(self, source, work, **options):
        options.pop("progress", None)
        work.mkdir(parents=True)
        (work / "frames").mkdir()
        (work / "frames/f000000.jpg").write_bytes(b"fixture image")
        with wave.open(str(work / "audio.wav"), "wb") as audio:
            audio.setparams((1, 2, 16000, 0, "NONE", "not compressed"))
            audio.writeframes(b"\0\0" * 16000)
        write_json(work / "manifest.json", {"duration": 1, "source_time_origin": 0,
            "source": {"name": source.name, "sha256": file_hash(source)}})
        write_json(work / "visual.json", {"parameters": options, "frames": [
            {"id": "f000000", "time": 0, "image": "frames/f000000.jpg", "reasons": ["periodic"]}], "events": []})

    def transcribe(self, work, **_):
        write_json(work / "transcript.raw.json", {"backend": "fixture", "model": "fixture", "segments": [
            {"id": "s000000", "start": 0, "end": 1, "text": " 원문을 보존합니다. Hello. Hola.", "words": []}]}, exclusive=True)

    def test_second_run_reuses_asr_and_media_and_exports_without_model_calls(self):
        with patch("avtimeline.media.prepare", side_effect=self.prep) as prep, \
             patch("avtimeline.transcript.transcribe", side_effect=self.transcribe) as asr:
            self.assertEqual("completed", run(self.args)["state"])
            before = (self.work / "transcript.raw.json").stat().st_mtime_ns
            self.assertEqual("completed", run(self.args)["state"])
            self.assertEqual(1, prep.call_count)
            self.assertEqual(1, asr.call_count)
            self.assertEqual(before, (self.work / "transcript.raw.json").stat().st_mtime_ns)

    def test_resume_after_interrupted_asr_and_partial_preparation(self):
        count = 0

        def interrupted(source, work, **options):
            nonlocal count
            count += 1
            if count == 1:
                work.mkdir(parents=True)
                (work / "audio.wav").write_bytes(b"incomplete")
                raise KeyboardInterrupt()
            self.prep(source, work, **options)

        with patch("avtimeline.media.prepare", side_effect=interrupted), \
             patch("avtimeline.transcript.transcribe", side_effect=[KeyboardInterrupt(), None]) as asr:
            with self.assertRaises(KeyboardInterrupt):
                run(self.args)
            self.assertEqual("interrupted", read_json(self.work / "pipeline.json")["stages"]["prepare"]["state"])
            with self.assertRaises(KeyboardInterrupt):
                run(self.args)
        with patch("avtimeline.media.prepare", side_effect=AssertionError("Media must not rerun")), \
             patch("avtimeline.transcript.transcribe", side_effect=self.transcribe):
            self.assertEqual("completed", run(self.args)["state"])

    def test_failed_pdf_does_not_block_other_formats_and_retry_only_exports(self):
        from avtimeline.export import export as real_export
        self.args.formats = "pdf,html,json"

        def partial(work, output, formats):
            if formats == ["pdf"]:
                raise ValueError("Chromium not installed")
            return real_export(work, output, formats)

        with patch("avtimeline.media.prepare", side_effect=self.prep), \
             patch("avtimeline.transcript.transcribe", side_effect=self.transcribe) as asr, \
             patch("avtimeline.export.export", side_effect=partial):
            result = run(self.args)
            self.assertEqual("failed", result["state"])
            self.assertTrue((self.work / "exports/index.html").exists())
            self.assertTrue((self.work / "exports/transcript.json").exists())
            self.args.formats = "html,json"
            self.assertEqual("completed", run(self.args)["state"])
            self.assertEqual(1, asr.call_count)

    def test_changed_input_settings_or_baseline_are_not_silently_reused(self):
        with patch("avtimeline.media.prepare", side_effect=self.prep), \
             patch("avtimeline.transcript.transcribe", side_effect=self.transcribe):
            run(self.args)
            self.args.language = "es"
            with self.assertRaisesRegex(ValueError, "ASR options changed"):
                run(self.args)
            self.args.language = "ko"
            data = read_json(self.work / "transcript.raw.json")
            data["segments"][0]["text"] = "changed"
            write_json(self.work / "transcript.raw.json", data)
            with self.assertRaisesRegex(ValueError, "Baseline transcript changed"):
                run(self.args)

    def test_lock_uses_live_os_handle_and_crashed_owner_releases_it(self):
        self.work.mkdir()
        with session_lock(self.work):
            with self.assertRaisesRegex(ValueError, "Another process"):
                with session_lock(self.work):
                    pass
        # A stale lock file is not a live lock.
        with session_lock(self.work):
            pass
        code = ("from pathlib import Path; import time; from avtimeline.pipeline import session_lock; "
                f"work=Path({str(self.work)!r})\n"
                "with session_lock(work):\n print('locked',flush=True)\n time.sleep(60)\n")
        env = {**os.environ, "PYTHONPATH": str(Path(__file__).resolve().parents[1] / "src")}
        child = subprocess.Popen([sys.executable, "-c", code], stdout=subprocess.PIPE, text=True, env=env)
        try:
            self.assertEqual("locked", child.stdout.readline().strip())
            self.assertIsNone(child.poll())
            with self.assertRaisesRegex(ValueError, "Another process"):
                with session_lock(self.work):
                    pass
            child.kill()
            child.wait(timeout=5)
            with session_lock(self.work):
                pass
        finally:
            if child.poll() is None:
                child.kill()
                child.wait()
            child.stdout.close()

    def test_progress_json_is_parseable_and_stage_reuse_is_reported(self):
        stream = io.StringIO()
        report = Progress("json", stream=stream)
        with patch("avtimeline.media.prepare", side_effect=self.prep), \
             patch("avtimeline.transcript.transcribe", side_effect=self.transcribe):
            run(self.args, reporter=report)
            run(self.args, reporter=report)
        events = [json.loads(line) for line in stream.getvalue().splitlines()]
        self.assertTrue(any(e["event"] == "reused" and e["stage"] == "transcribe" for e in events))
        self.assertTrue(any(e["event"] == "completed" and e["stage"] == "export:html" for e in events))

    def test_batch_continues_after_missing_input_and_reuses_successful_item(self):
        args = parser().parse_args(["batch", str(self.root / "missing.mp4"), str(self.source),
            "--output-root", str(self.root / "batch"), "--backend", "faster-whisper", "--formats", "html", "--progress", "quiet"])
        with patch("avtimeline.media.prepare", side_effect=self.prep), \
             patch("avtimeline.transcript.transcribe", side_effect=self.transcribe) as asr:
            first = batch(args)
            second = batch(args)
            self.assertEqual(["failed", "completed"], [r["state"] for r in first["items"]])
            self.assertEqual(1, asr.call_count)
            self.assertEqual(first["items"][1]["work"], second["items"][1]["work"])
            self.assertTrue((args.output_root / "batch.json").exists())


if __name__ == "__main__":
    unittest.main()
