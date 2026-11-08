from __future__ import annotations

from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from avtimeline.cloud import mount
from avtimeline.intake import resolve


class CloudMountTests(unittest.TestCase):
    def test_mount_uses_existing_auth_read_only_and_no_disk_cache(self):
        with tempfile.TemporaryDirectory() as folder:
            point = Path(folder) / "cloud mount"
            for remote in ("gdrive:Recorded talks", "onedrive:Recorded talks"):
                with patch("avtimeline.cloud.shutil.which", return_value="rclone"), \
                     patch("avtimeline.cloud.subprocess.run") as run:
                    mount(remote, str(point))
                argv = run.call_args.args[0]
                self.assertEqual(["rclone", "mount", remote, str(point)], argv[:4])
                self.assertIn("--read-only", argv)
                self.assertEqual("off", argv[argv.index("--vfs-cache-mode") + 1])
                self.assertNotIn("--config", argv)
                self.assertNotIn("shell", run.call_args.kwargs)
            (point / "owned.txt").write_text("keep", encoding="utf-8")
            with patch("avtimeline.cloud.shutil.which", return_value="rclone"), \
                 patch("avtimeline.cloud.subprocess.run") as run:
                with self.assertRaisesRegex(ValueError, "empty"):
                    mount("gdrive:", str(point))
                run.assert_not_called()
            self.assertEqual("keep", (point / "owned.txt").read_text(encoding="utf-8"))

    def test_missing_tool_and_invalid_remote_have_actionable_errors(self):
        with patch("avtimeline.cloud.shutil.which", return_value=None):
            with self.assertRaisesRegex(ValueError, "rclone config"):
                mount("onedrive:", "unused")
        with self.assertRaisesRegex(ValueError, "configured rclone remote"):
            mount("https://drive.example/video", "unused")

    def test_windows_drive_mount_is_passed_to_rclone_without_creating_a_directory(self):
        with patch("avtimeline.cloud.os.name", "nt"), \
             patch("avtimeline.cloud.shutil.which", return_value="rclone"), \
             patch("avtimeline.cloud.subprocess.run") as run:
            mount("onedrive:Recordings", "X:")
        self.assertEqual("X:", run.call_args.args[0][3])

    def test_mounted_input_is_read_in_place_without_an_intake_copy(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            mountpoint = root / "mounted"
            mountpoint.mkdir()
            recording = mountpoint / "lecture.mp4"
            recording.write_bytes(b"remote filesystem fixture")
            work = root / "session"
            self.assertEqual(recording.resolve(), resolve(str(recording), work))
            self.assertFalse(work.exists())
            recording.unlink()
            with self.assertRaisesRegex(ValueError, "mount"):
                resolve(str(recording), work)


if __name__ == "__main__":
    unittest.main()
