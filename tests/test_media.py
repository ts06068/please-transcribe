from __future__ import annotations

import importlib.util
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
import wave

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from avtimeline.media import prepare
from avtimeline.storage import read_json

AVAILABLE = all(importlib.util.find_spec(m) for m in ("av", "numpy", "PIL")) and all(
    shutil.which(exe) for exe in ("ffmpeg", "ffprobe"))


@unittest.skipUnless(AVAILABLE, "Requires media extra and FFmpeg executables")
class MediaTests(unittest.TestCase):
    def test_dense_evidence_and_audio_follow_original_offset_and_are_citable(self):
        from avtimeline.evidence import dense_frames, clip_audio
        from avtimeline.timeline import build
        from avtimeline.storage import write_json
        from avtimeline.review import import_response
        from avtimeline.export import export

        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            source = root / "offset.mkv"
            subprocess.run([
                "ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", "testsrc2=s=160x96:r=10:d=4",
                "-itsoffset", "1", "-f", "lavfi", "-i", "sine=sample_rate=16000:duration=3",
                "-map", "0:v", "-map", "1:a", "-c:v", "ffv1", "-c:a", "pcm_s16le",
                "-output_ts_offset", "5", str(source)], check=True)
            work = root / "session"
            prepare(source, work, sample_interval=0.5, image_interval=2, change_threshold=0.08,
                    tile_threshold=0.16, min_event_gap=1, crop=None, ocr_languages=None)
            raw = {"language": "en", "segments": [{"id": "s000000", "start": 1, "end": 3,
                                                     "text": "original text", "words": []}]}
            write_json(work / "transcript.raw.json", raw)
            data = build(work)
            before = (work / "transcript.raw.json").read_bytes()
            result = dense_frames(work, 1.5, 2.5, interval=0.2, max_frames=10)
            self.assertGreaterEqual(len(result["frames"]), 4)
            self.assertTrue(all(1.5 <= frame["time"] <= 2.5 for frame in result["frames"]))
            report = {"snapshot": data["snapshot"], "reviewer": "test reviewer", "corrections": [{
                "segment_id": "s000000", "original_text": "original text", "proposed_text": "proposed term",
                "reason": "Inspect this dense frame", "evidence_frame_ids": [result["frames"][0]["id"]]}],
                "event_annotations": [], "uncertainties": []}
            import_response(work, report)
            clip = clip_audio(work, 0.5, 1.5)
            import numpy as np
            with wave.open(str(work / clip["audio"])) as audio:
                samples = np.frombuffer(audio.readframes(audio.getnframes()), dtype=np.int16)
            self.assertLess(abs(samples[:6000]).max(), 10)
            self.assertGreater(abs(samples[9000:]).max(), 1000)
            export(work, work / "exports", ["md", "html", "json"])
            self.assertIn(result["frames"][0]["id"], (work / "exports/index.html").read_text())
            exported = read_json(work / "exports/transcript.json")
            self.assertEqual(len(result["frames"]), len(exported["inspection"]))
            self.assertTrue((work / "exports" / result["frames"][0]["image"]).is_file())
            self.assertEqual(before, (work / "transcript.raw.json").read_bytes())

    def test_audio_offset_and_visual_changes_use_same_timeline(self):
        import numpy as np

        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            source = root / "offset.mkv"
            subprocess.run([
                "ffmpeg", "-v", "error", "-nostdin", "-y",
                "-f", "lavfi", "-i", "color=c=red:s=160x96:r=10:d=2",
                "-f", "lavfi", "-i", "color=c=blue:s=160x96:r=10:d=2",
                "-itsoffset", "1", "-f", "lavfi", "-i", "sine=frequency=440:sample_rate=16000:duration=3",
                "-filter_complex", "[0:v][1:v]concat=n=2:v=1:a=0[v]", "-map", "[v]", "-map", "2:a",
                "-c:v", "ffv1", "-c:a", "pcm_s16le", str(source),
            ], check=True)
            work = root / "session"
            prepare(source, work, sample_interval=0.2, image_interval=2,
                    change_threshold=0.05, tile_threshold=0.16, min_event_gap=0.5,
                    crop=None, ocr_languages=None)
            manifest = read_json(work / "manifest.json")
            self.assertAlmostEqual(4, manifest["duration"], delta=0.15)
            with wave.open(str(work / "audio.wav")) as wav:
                self.assertEqual(16000, wav.getframerate())
                audio = np.frombuffer(wav.readframes(wav.getnframes()), dtype=np.int16)
            self.assertLess(np.abs(audio[:14000]).max(), 10)
            self.assertGreater(np.abs(audio[17000:23000]).max(), 1000)
            visual = read_json(work / "visual.json")
            self.assertTrue(any(abs(event["time"] - 2) < 0.3 for event in visual["events"]))
            for frame in visual["frames"]:
                self.assertTrue((work / frame["image"]).exists())
            self.assertNotIn(str(root), (work / "manifest.json").read_text(encoding="utf-8"))
            with self.assertRaises(ValueError):
                prepare(source, work)

    def test_source_with_nonzero_origin_keeps_relative_audio_offset(self):
        import numpy as np

        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            source = root / "shifted.mkv"
            subprocess.run([
                "ffmpeg", "-v", "error", "-nostdin", "-y",
                "-f", "lavfi", "-i", "color=c=white:s=160x96:r=10:d=3",
                "-itsoffset", "1", "-f", "lavfi", "-i", "sine=frequency=440:sample_rate=16000:duration=2",
                "-map", "0:v", "-map", "1:a", "-c:v", "ffv1", "-c:a", "pcm_s16le",
                "-output_ts_offset", "5", str(source),
            ], check=True)
            work = root / "session"
            prepare(source, work, sample_interval=0.2, image_interval=2,
                    change_threshold=0.05, tile_threshold=0.16, min_event_gap=0.5,
                    crop=None, ocr_languages=None)
            manifest = read_json(work / "manifest.json")
            self.assertAlmostEqual(5, manifest["source_time_origin"], delta=0.1)
            self.assertAlmostEqual(3, manifest["duration"], delta=0.15)
            with wave.open(str(work / "audio.wav")) as wav:
                audio = np.frombuffer(wav.readframes(wav.getnframes()), dtype=np.int16)
            self.assertLess(np.abs(audio[:14000]).max(), 10)
            self.assertGreater(np.abs(audio[17000:23000]).max(), 1000)
            visual = read_json(work / "visual.json")
            self.assertAlmostEqual(0, visual["frames"][0]["time"], delta=0.15)


if __name__ == "__main__":
    unittest.main()
