from __future__ import annotations

import json
import sys
import threading
import time


class Progress:
    """Progress belongs on stderr; stdout remains usable for machine results."""

    def __init__(self, mode: str = "text", *, stream=None):
        self.mode, self.stream = mode, stream or sys.stderr
        self.lock = threading.Lock()
        self.last_update = 0.0

    def emit(self, event: str, stage: str, **details):
        if self.mode == "quiet":
            return
        data = {"event": event, "stage": stage, **details}
        with self.lock:
            if self.mode == "json":
                print(json.dumps(data, ensure_ascii=False), file=self.stream, flush=True)
            else:
                status = details.get("state") or details.get("message") or ""
                elapsed = f" ({details['elapsed']:.1f}s)" if "elapsed" in details else ""
                print(f"[{stage}] {event}{elapsed}" + (f": {status}" if status else ""), file=self.stream, flush=True)

    def update(self, stage: str, **details):
        now = time.monotonic()
        if now - self.last_update >= 1:
            self.last_update = now
            self.emit("progress", stage, **details)

    def heartbeat(self, stage: str, stopped: threading.Event, started: float):
        while not stopped.wait(5):
            self.emit("working", stage, elapsed=time.monotonic() - started)
