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
