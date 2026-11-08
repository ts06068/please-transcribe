from __future__ import annotations

import io
import contextlib
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import tempfile
import threading
import time
import unittest
import urllib.error
import urllib.request
from unittest.mock import patch
import wave

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from avtimeline.connection import request, save_connection, upload, remote_transcribe
from avtimeline.model_service import Supervisor, process_job
from avtimeline.server_install import bundle_source
from avtimeline.service_api import API
from avtimeline.storage import read_json, write_json


class ServiceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        (self.root / "control").mkdir()
        write_json(self.root / "keys.json", {"alice": "a-secret", "bob": "b-secret"})
        self.config = {"backend": "faster-whisper", "model": "fixture-model",
                       "max_upload_bytes": 100000, "max_pending": 2, "retention_days": 7}
        self.server = API(("127.0.0.1", 0), queue=self.root / "queue", control=self.root / "control",
                          keys=self.root / "keys.json", config=self.config)
        # Server binding may perform slow hostname resolution on a CI runner.
        # Start the simulated worker heartbeat after binding so it is fresh when
        # the first request is made, as a live worker's heartbeat would be.
        write_json(self.root / "control/status.json", {"state": "ready", "updated": time.time()})
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.connection = {"endpoint": f"http://127.0.0.1:{self.server.server_port}", "api_key": "a-secret"}
        self.audio = self.root / "audio.wav"
        with wave.open(str(self.audio), "wb") as audio:
            audio.setparams((1, 2, 16000, 0, "NONE", "not compressed"))
            audio.writeframes(b"\0\0" * 16000)

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join()
        self.temp.cleanup()

    def test_auth_and_job_owner_isolation(self):
        self.assertEqual("fixture-model", request(self.connection, "/v1/models")["model"])
        with self.assertRaisesRegex(ValueError, "401"):
            request({**self.connection, "api_key": "invalid"}, "/v1/models")
        job = upload(self.connection, self.audio, "ko", False)
        self.assertEqual("queued", request(self.connection, "/v1/jobs/" + job["id"])["state"])
        with self.assertRaisesRegex(ValueError, "404"):
            request({**self.connection, "api_key": "b-secret"}, "/v1/jobs/" + job["id"])
        with self.assertRaisesRegex(ValueError, "409"):
            request(self.connection, "/v1/jobs/" + job["id"] + "/result")

    def test_real_http_upload_worker_result_and_private_profile(self):
        profile = self.root / "connection.json"
        save_connection(self.connection, profile)
        if os.name == "posix":
            self.assertEqual(0o600, profile.stat().st_mode & 0o777)
        job = upload(self.connection, self.audio, "ko", False)
        directory = self.root / "queue/jobs" / job["id"]
        process_job(directory, lambda *_: {"language": "ko", "segments": [
            {"start": 0, "end": 1, "text": " 안녕하세요. Hello. Hola.",
             "words": [{"start": 0, "end": 0.5, "word": " 안녕하세요."}]}]}, self.config)
        result = request(self.connection, "/v1/jobs/" + job["id"] + "/result")
        self.assertEqual(" 안녕하세요. Hello. Hola.", result["segments"][0]["text"])
        self.assertFalse((directory / "audio.wav").exists())
        self.assertTrue(self.audio.exists())

    def test_remote_poll_timeout_resumes_same_job_without_duplicate_upload(self):
        profile = self.root / "connection.json"
        save_connection(self.connection, profile)
        with self.assertRaisesRegex(ValueError, "resume"):
            remote_transcribe(self.root, endpoint=None, config=profile, model=None, language="auto",
                              multilingual=False, timeout=0.01, poll_interval=0.02)
        jobs = list((self.root / "queue/jobs").iterdir())
        self.assertEqual(1, len(jobs))
        process_job(jobs[0], lambda *_: {"language": "en", "segments": [
            {"start": 0, "end": 1, "text": "hello"}]}, self.config)
        result = remote_transcribe(self.root, endpoint=None, config=profile, model=None,
                                  language="auto", multilingual=False)
        self.assertEqual("hello", result["segments"][0]["text"])
        self.assertEqual(1, len(list((self.root / "queue/jobs").iterdir())))
        # Even losing the receipt after server acceptance must not create another
        # model job; the same upload identity is idempotent on the server.
        (self.root / "remote-job.json").unlink()
        resumed = remote_transcribe(self.root, endpoint=None, config=profile, model=None,
                                    language="auto", multilingual=False)
        self.assertEqual(result, resumed)
        self.assertEqual(1, len(list((self.root / "queue/jobs").iterdir())))

    def test_api_restart_reclaims_unpublished_uploads_and_keeps_queued_jobs(self):
        job = upload(self.connection, self.audio, "en", False)
        orphan = self.root / "queue/jobs" / ("a" * 32)
        orphan.mkdir()
        write_json(orphan / "status.json", {"state": "uploading"})
        (orphan / "audio.wav").write_bytes(b"partial")
        replacement = API(("127.0.0.1", 0), queue=self.root / "queue", control=self.root / "control",
                          keys=self.root / "keys.json", config=self.config)
        replacement.server_close()
        self.assertFalse(orphan.exists())
        self.assertTrue((self.root / "queue/jobs" / job["id"] / "request.json").exists())

    def test_explicit_retry_recovers_failed_job_and_preserves_attempt_history(self):
        profile = self.root / "connection.json"
        save_connection(self.connection, profile)
        with self.assertRaisesRegex(ValueError, "resume"):
            remote_transcribe(self.root, endpoint=None, config=profile, model=None, language="auto",
                              multilingual=False, timeout=0.01, poll_interval=0.02)
        directory = next((self.root / "queue/jobs").iterdir())
        with contextlib.redirect_stderr(io.StringIO()):
            process_job(directory, lambda *_: (_ for _ in ()).throw(RuntimeError("Transient engine error")), self.config)
        with self.assertRaisesRegex(ValueError, "retry-failed"):
            remote_transcribe(self.root, endpoint=None, config=profile, model=None, language="auto", multilingual=False)
        done = threading.Event()

        def recover():
            for _ in range(200):
                try:
                    status = read_json(directory / "status.json")
                except (OSError, ValueError):
                    status = {}
                if status.get("state") == "queued" and (directory / "request.json").exists():
                    process_job(directory, lambda *_: {"language": "en", "segments": [
                        {"start": 0, "end": 1, "text": "recovered"}]}, self.config)
                    done.set()
                    return
                time.sleep(0.01)

        thread = threading.Thread(target=recover, daemon=True)
        thread.start()
        result = remote_transcribe(self.root, endpoint=None, config=profile, model=None, language="auto",
                                   multilingual=False, retry_failed=True, poll_interval=0.01, timeout=3)
        thread.join(timeout=3)
        self.assertTrue(done.is_set())
        self.assertEqual("recovered", result["segments"][0]["text"])
        self.assertEqual(1, len(list((self.root / "queue/attempts").iterdir())))
        # A retained client receipt can also outlive an expired server result.
        import shutil
        shutil.rmtree(directory)
        done.clear()
        thread = threading.Thread(target=recover, daemon=True)
        thread.start()
        restored = remote_transcribe(self.root, endpoint=None, config=profile, model=None, language="auto",
                                     multilingual=False, retry_failed=True, poll_interval=0.01, timeout=3)
        thread.join(timeout=3)
        self.assertTrue(done.is_set())
        self.assertEqual(result, restored)

    def test_stopped_worker_bad_wav_and_full_queue_are_explicit_errors(self):
        write_json(self.root / "control/status.json", {"state": "stopped", "updated": time.time()})
        with self.assertRaisesRegex(ValueError, "503"):
            upload(self.connection, self.audio, "en", False)
        write_json(self.root / "control/status.json", {"state": "ready", "updated": time.time()})
        invalid = self.root / "invalid.wav"
        invalid.write_bytes(b"not a WAV")
        with self.assertRaisesRegex(ValueError, "422"):
            upload(self.connection, invalid, "en", False)
        self.assertEqual(0, len(list((self.root / "queue/jobs").iterdir())))
        upload(self.connection, self.audio, "en", False)
        upload(self.connection, self.audio, "en", False)
        with self.assertRaisesRegex(ValueError, "429"):
            upload(self.connection, self.audio, "en", False)

    def test_model_and_multilingual_mismatch_rejected_before_upload(self):
        profile = self.root / "connection.json"
        save_connection(self.connection, profile)
        with self.assertRaisesRegex(ValueError, "requested model"):
            remote_transcribe(self.root, endpoint=None, config=profile, model="other", language="auto", multilingual=False)
        self.server.config["backend"] = "mlx"
        with self.assertRaisesRegex(ValueError, "language switching"):
            remote_transcribe(self.root, endpoint=None, config=profile, model=None, language="auto", multilingual=True)
        self.assertEqual(0, len(list((self.root / "queue/jobs").iterdir())))

    def test_install_archive_contains_only_program_source_and_metadata(self):
        import tarfile
        payload, identity = bundle_source()
        second, second_identity = bundle_source()
        self.assertEqual(identity, second_identity)
        with tarfile.open(fileobj=io.BytesIO(payload)) as archive:
            names = archive.getnames()
        self.assertIn("src/avtimeline/service_api.py", names)
        self.assertTrue(all(name.startswith("src/avtimeline/") or name in ("README.md", "pyproject.toml", "LICENSE") for name in names))
        self.assertFalse(any(".env" in name or "client.json" in name or "api-keys" in name for name in names))

    @unittest.skipUnless(os.name == "posix", "POSIX native worker supervisor")
    def test_expired_docker_lease_terminates_actual_worker_process(self):
        state = self.root / "native"
        (state / "control").mkdir(parents=True)
        (state / "queue").mkdir()
        token = "a" * 32
        lease = state / "control/lease.json"
        write_json(lease, {"token": token})
        real_popen = subprocess.Popen

        def lightweight_worker(*_, **kwargs):
            return real_popen([sys.executable, "-c", "import time; time.sleep(60)"], **kwargs)

        supervisor = Supervisor(state)
        try:
            with patch("avtimeline.model_service.subprocess.Popen", side_effect=lightweight_worker):
                supervisor.tick()
            pid = supervisor.process.pid
            os.kill(pid, 0)
            os.utime(lease, (time.time() - 20, time.time() - 20))
            supervisor.tick()
            self.assertIsNone(supervisor.process)
            self.assertEqual("stopped", read_json(state / "control/status.json")["state"])
            with self.assertRaises(ProcessLookupError):
                os.kill(pid, 0)
        finally:
            supervisor.stop()
            supervisor.log.close()


if __name__ == "__main__":
    unittest.main()
