from __future__ import annotations

import importlib.util
import platform
import shutil
import sys
from pathlib import Path


def diagnose(*, endpoint: str | None = None, connection: Path | None = None,
             backend: str | None = None, pdf: bool = False, url: bool = False,
             server_only: bool = False) -> dict:
    from .connection import credentials, default_config, request
    import os

    checks = []

    def add(name, okay, action=None, **details):
        checks.append({"name": name, "okay": bool(okay), **details,
                       **({"action": action} if not okay and action else {})})

    add("python", sys.version_info >= (3, 10), "Install Python 3.10 or newer", version=platform.python_version())
    if not server_only:
        for binary in ("ffmpeg", "ffprobe"):
            path = shutil.which(binary)
            add(binary, path is not None, "Install FFmpeg and put its executables on PATH", path=path)
        for module in ("av", "numpy", "PIL"):
            add(module, importlib.util.find_spec(module) is not None, "python -m pip install '.[media]'")
    remote = backend == "remote" or (backend is None and
             (endpoint or os.environ.get("PLEASE_TRANSCRIBE_ENDPOINT") or (connection or default_config()).exists()))
    report = {"platform": platform.system(), "architecture": platform.machine(),
              "backend": "remote" if remote or server_only else backend or "faster-whisper", "checks": checks}
    if remote or server_only:
        try:
            cfg = credentials(endpoint, connection)
            capabilities = request(cfg, "/v1/models")
            health = request(cfg, "/healthz")
            report.update(endpoint=cfg["endpoint"], capabilities=capabilities, health=health)
            add("server_authentication", True)
            add("model_ready", health["model"]["state"] in ("ready", "busy"),
                "Ask the server administrator to start/check model-control and inspect worker.log",
                state=health["model"]["state"])
            report["language_note"] = ("multilingual means per-window language detection; "
                                        "auto/selected-language transcription can support many languages independently")
        except (OSError, ValueError) as exc:
            add("remote_server", False, "Use connect to set a reachable endpoint and valid API key", error=str(exc))
    else:
        module = "mlx_whisper" if backend == "mlx" else "faster_whisper"
        add(module, importlib.util.find_spec(module) is not None,
            "python -m pip install '.[mlx]'" if backend == "mlx" else "python -m pip install '.[cpu]'")
        if backend == "mlx":
            add("apple_silicon_host", platform.system() == "Darwin" and platform.machine() == "arm64",
                "Use a native Apple Silicon macOS host, or connect to a remote server")
    if url:
        add("yt_dlp", importlib.util.find_spec("yt_dlp") is not None, "python -m pip install '.[url]'")
        from .intake import javascript_runtime
        add("yt_dlp_ejs", importlib.util.find_spec("yt_dlp_ejs") is not None,
            "python -m pip install -U '.[url]'")
        runtime = javascript_runtime()
        add("youtube_javascript_runtime", runtime is not None, "Install Node.js 22+ or Deno 2.3+ for YouTube",
            runtime=runtime)
    if pdf:
        add("pymupdf", importlib.util.find_spec("pymupdf") is not None, "python -m pip install '.[pdf]'")
        installed = importlib.util.find_spec("playwright") is not None
        add("playwright", installed, "python -m pip install '.[pdf]'")
        if installed:
            try:
                from playwright.sync_api import sync_playwright
                with sync_playwright() as p:
                    browser = p.chromium.launch()
                    browser.close()
                add("chromium", True)
            except Exception as exc:
                add("chromium", False, "python -m playwright install --with-deps chromium", error=str(exc).splitlines()[0])
    report["okay"] = all(check["okay"] for check in checks)
    return report
