from __future__ import annotations

import io
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from avtimeline.diagnostics import diagnose
from avtimeline.connection import save_connection


class DiagnosticTests(unittest.TestCase):
    def test_unrelated_llm_endpoint_is_not_saved_as_a_transcription_profile(self):
        with tempfile.TemporaryDirectory() as folder:
            profile = Path(folder) / "client.json"
            with patch("urllib.request.urlopen", return_value=io.BytesIO(b'{"object":"list","data":[]}')):
                with self.assertRaisesRegex(ValueError, "not a please-transcribe"):
                    save_connection({"endpoint": "http://wrong-server", "api_key": "private"}, profile)
            self.assertFalse(profile.exists())

    def test_missing_dependencies_produce_actions_instead_of_tracebacks(self):
        with patch("avtimeline.diagnostics.shutil.which", return_value=None), \
             patch("avtimeline.diagnostics.importlib.util.find_spec", return_value=None):
            result = diagnose(backend="faster-whisper", pdf=True, url=True)
        self.assertFalse(result["okay"])
        missing = {check["name"]: check for check in result["checks"] if not check["okay"]}
        self.assertTrue({"ffmpeg", "ffprobe", "av", "faster_whisper", "playwright", "yt_dlp"} <= missing.keys())
        self.assertTrue(all(item.get("action") for item in missing.values()))

    def test_server_only_checks_auth_and_readiness_without_local_media(self):
        with tempfile.TemporaryDirectory() as folder:
            profile = Path(folder) / "client.json"
            profile.write_text(json.dumps({"endpoint": "http://private-server:8765", "api_key": "never-print-this"}))
            with patch("avtimeline.connection.request", side_effect=[
                {"model": "large-v3", "multilingual": False}, {"api": "ready", "model": {"state": "stopped"}}]):
                result = diagnose(connection=profile, server_only=True)
        self.assertFalse(result["okay"])
        self.assertNotIn("never-print-this", json.dumps(result))
        self.assertNotIn("ffmpeg", {check["name"] for check in result["checks"]})
        self.assertIn("model_ready", {check["name"] for check in result["checks"] if not check["okay"]})
