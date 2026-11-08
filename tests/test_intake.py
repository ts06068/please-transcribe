from __future__ import annotations

from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
import importlib.util
from pathlib import Path
import sys
import tempfile
import threading
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from avtimeline.intake import resolve


@unittest.skipUnless(importlib.util.find_spec("yt_dlp"), "Optional URL downloader")
class URLTests(unittest.TestCase):
    def test_direct_url_is_downloaded_and_resumed_without_repeated_fetch(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            (root / "owned.mp4").write_bytes(b"owned synthetic media fixture")
            counts = []

            class Handler(SimpleHTTPRequestHandler):
                def do_GET(self):
                    counts.append(self.path)
                    super().do_GET()

                def log_message(self, *_):
                    pass

            server = ThreadingHTTPServer(("127.0.0.1", 0), partial(Handler, directory=str(root)))
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                url = f"http://127.0.0.1:{server.server_port}/owned.mp4"
                work = root / "session"
                path = resolve(url, work)
                self.assertEqual((root / "owned.mp4").read_bytes(), path.read_bytes())
                first = len(counts)
                self.assertEqual(path, resolve(url, work))
                self.assertEqual(first, len(counts))
                with self.assertRaisesRegex(ValueError, "different URL"):
                    resolve(url + "?different", work)
            finally:
                server.shutdown()
                server.server_close()
                thread.join()


class YouTubeTests(unittest.TestCase):
    def test_youtube_watch_short_and_shorts_links_are_recognized(self):
        from avtimeline.intake import is_youtube
        for url in ("https://www.youtube.com/watch?v=fixture", "https://youtu.be/fixture",
                    "https://youtube.com/shorts/fixture", "https://m.youtube.com/watch?v=fixture"):
            self.assertTrue(is_youtube(url))
        self.assertFalse(is_youtube("https://youtube.com.example.org/video"))

    def test_runtime_selection_checks_supported_versions(self):
        from types import SimpleNamespace
        from unittest.mock import patch
        from avtimeline.intake import javascript_runtime
        with patch("avtimeline.intake.shutil.which", side_effect=lambda name: name), \
             patch("avtimeline.intake.subprocess.run", side_effect=[
                 SimpleNamespace(returncode=0, stdout="deno 1.0.0"),
                 SimpleNamespace(returncode=0, stdout="v22.1.0")]):
            self.assertEqual("node", javascript_runtime()["name"])
        with patch("avtimeline.intake.shutil.which", return_value=None):
            self.assertIsNone(javascript_runtime())

    @unittest.skipUnless(importlib.util.find_spec("yt_dlp"), "Optional URL downloader")
    def test_youtube_download_is_one_video_cached_and_uses_javascript_solver(self):
        from types import SimpleNamespace
        from unittest.mock import patch
        import json
        with tempfile.TemporaryDirectory(prefix="pt-영상-") as folder:
            work = Path(folder)
            saved = work / "intake/source.mp4"
            calls = []

            def process(argv, **kwargs):
                calls.append(argv)
                if "--dump-single-json" in argv:
                    stdout = json.dumps({"_type": "video", "id": "fixture"})
                else:
                    saved.write_bytes(b"synthetic downloaded video fixture")
                    stdout = str(saved) + "\n"
                return SimpleNamespace(returncode=0, communicate=lambda: (stdout, ""))

            with patch("avtimeline.intake.javascript_runtime", return_value={"name": "node"}), \
                 patch("avtimeline.intake.importlib.util.find_spec", return_value=True), \
                 patch("avtimeline.intake.subprocess.Popen", side_effect=process):
                self.assertEqual(saved.resolve(), resolve("https://youtu.be/fixture", work))
                self.assertEqual(saved.resolve(), resolve("https://youtu.be/fixture", work))
            self.assertEqual(2, len(calls))  # Metadata + one download, then cache reuse.
            self.assertTrue(all("--no-playlist" in call and "--js-runtimes" in call for call in calls))
            self.assertTrue(all(call[call.index("--js-runtimes") + 1] == "node" for call in calls))
