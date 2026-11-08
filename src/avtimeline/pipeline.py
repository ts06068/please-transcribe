"""Durable stages, verified reuse, progress, and partial batch failures."""
from __future__ import annotations

from contextlib import contextmanager
import json
import os
from pathlib import Path
import shutil
import socket
import threading
import time
import uuid

from .storage import file_hash, fingerprint, read_json, write_json
from .progress import Progress


@contextmanager
def session_lock(work: Path, *, name: str = ".pipeline.lock"):
    work.mkdir(parents=True, exist_ok=True)
    path = work / name
    handle = path.open("a+b")
    try:
        if os.name == "nt":
            import msvcrt
            if path.stat().st_size == 0:
                handle.write(b" ")
                handle.flush()
            handle.seek(0)
            msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            import fcntl
            fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        handle.close()
        raise ValueError("Another process is using this session") from None
    handle.seek(0)
    handle.truncate()
    handle.write(json.dumps({"pid": os.getpid(), "host": socket.gethostname()}).encode())
    handle.flush()
    try:
        yield
    finally:
        # OS locks are released on close/crash. Retain the inode so a new opener
        # cannot race with unlink and accidentally acquire a second lock.
        if os.name == "nt":
            handle.seek(0)
            msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
        handle.close()


def artifacts(work: Path, names: list[str]) -> dict:
    return {name: file_hash(work / name) for name in names}


class Pipeline:
    def __init__(self, work: Path, progress: Progress):
        self.work, self.progress = work, progress
        self.path = work / "pipeline.json"
        self.data = read_json(self.path) if self.path.exists() else {"schema_version": "1.0", "stages": {}}

    def stage(self, name: str, inputs: object, operation):
        identity = fingerprint(inputs)
        old = self.data["stages"].get(name, {})
        if old.get("state") == "completed" and old.get("input") == identity:
            if all((self.work / p).is_file() and file_hash(self.work / p) == digest
                   for p, digest in old["artifacts"].items()):
                self.progress.emit("reused", name)
                return
            if name == "transcribe" and (self.work / "transcript.raw.json").exists():
                raise ValueError("Baseline transcript changed since the previous run; refusing to replace its history")
        started = time.monotonic()
        record = {"input": identity, "state": "running", "started": time.time()}
        self.data["stages"][name] = record
        write_json(self.path, self.data)
        self.progress.emit("started", name)
        stopped = threading.Event()
        heartbeat = threading.Thread(target=self.progress.heartbeat, args=(name, stopped, started), daemon=True)
        heartbeat.start()
        try:
            outputs = operation()
            record.update(state="completed", artifacts=artifacts(self.work, outputs))
        except BaseException as exc:
            record.update(state="interrupted" if isinstance(exc, (KeyboardInterrupt, SystemExit)) else "failed",
                          error=type(exc).__name__)
            self.progress.emit(record["state"], name)
            raise
        finally:
            stopped.set()
            heartbeat.join()
            record["elapsed"] = time.monotonic() - started
            write_json(self.path, self.data)
        self.progress.emit("completed", name, elapsed=record["elapsed"])


def _run_base(args, *, reporter: Progress | None = None) -> dict:
    from . import intake
    from .media import prepare
    from .transcript import transcribe, speech_audit
    from .timeline import build
    from .review import bundle
    from .export import export, FORMATS

    work = args.work.resolve()
    formats = [fmt.strip() for fmt in args.formats.split(",") if fmt.strip()]
    if not formats or set(formats) - FORMATS:
        raise ValueError("Choose supported output formats: " + ",".join(sorted(FORMATS)))
    progress = reporter or Progress(getattr(args, "progress", "text"))
    with session_lock(work):
        pipeline = Pipeline(work, progress)
        was_managed = pipeline.path.exists()
        if not was_managed and not (work / "manifest.json").exists() and any(
                (work / name).exists() for name in ("audio.wav", "frames", "visual.json", "transcript.raw.json")):
            raise ValueError("Unmanaged partial session found; choose a new directory to preserve existing files")
        source_value = str(args.source)
        input_id = fingerprint(source_value if intake.is_url(source_value) else str(Path(source_value).expanduser().resolve()))
        if pipeline.data.get("source_input") not in (None, input_id):
            raise ValueError("Session belongs to a different input; use another work directory")
        pipeline.data["source_input"] = input_id
        write_json(pipeline.path, pipeline.data)
        progress.emit("started", "input")
        input_stopped = threading.Event()
        input_heartbeat = threading.Thread(target=progress.heartbeat,
            args=("input", input_stopped, time.monotonic()), daemon=True)
        input_heartbeat.start()
        try:
            source = intake.resolve(source_value, work, progress)
            source_hash = file_hash(source)
        finally:
            input_stopped.set()
            input_heartbeat.join()
        if pipeline.data.get("source_sha256") not in (None, source_hash):
            raise ValueError("Source recording changed; use a new session to preserve the old baseline")
        pipeline.data["source_sha256"] = source_hash
        pipeline.data["source_path"] = str(source)
        write_json(pipeline.path, pipeline.data)
        progress.emit("completed", "input")
        visual_names = ("sample_interval", "image_interval", "change_threshold", "tile_threshold",
                        "min_event_gap", "crop", "ocr_languages")
        visual_options = {key: getattr(args, key) for key in visual_names}
        old_options = pipeline.data.get("visual_options")
        if old_options is not None and old_options != json.loads(json.dumps(visual_options)):
            raise ValueError("Screen extraction options changed; use a new session")
        pipeline.data["visual_options"] = visual_options

        def prep():
            # Adopt a valid session prepared with the standalone command.
            if (work / "manifest.json").exists():
                manifest = read_json(work / "manifest.json")
                if manifest["source"]["sha256"] != source_hash:
                    raise ValueError("Prepared session belongs to another source")
                if read_json(work / "visual.json")["parameters"] != json.loads(json.dumps(visual_options)):
                    raise ValueError("Prepared screen options differ from the requested options")
            else:
                staging = work / ".preparing"
                # This directory is reserved for unfinished pipeline-owned output.
                if staging.exists():
                    shutil.rmtree(staging)
                prepare(source, staging, **visual_options,
                        progress=lambda **kw: progress.update("prepare", **kw))
                for name in ("audio.wav", "frames", "visual.json", "manifest.json"):
                    target = work / name
                    if target.exists():
                        if target.is_dir():
                            shutil.rmtree(target)
                        else:
                            target.unlink()
                    shutil.move(str(staging / name), str(target))
                if (staging / "source.json").exists():
                    shutil.move(str(staging / "source.json"), str(work / "source.json"))
                staging.rmdir()
            visual = read_json(work / "visual.json")
            return ["manifest.json", "audio.wav", "visual.json"] + [f["image"] for f in visual["frames"]]

        pipeline.stage("prepare", {"source": source_hash, "options": visual_options}, prep)
        asr_options = {key: getattr(args, key) for key in
                       ("backend", "model", "language", "multilingual", "device", "compute_type", "endpoint")}
        # Profile content is intentionally excluded from progress and state. Use
        # public server identity, never an API key, to prevent accidental mixing.
        from .connection import default_config, credentials, request
        profile = args.connection or default_config()
        backend = args.backend
        imported = getattr(args, "import_transcript", None)
        if imported:
            backend = "import"
        if backend is None:
            backend = "remote" if args.endpoint or os.environ.get("PLEASE_TRANSCRIBE_ENDPOINT") or profile.exists() else "faster-whisper"
        asr_options["backend"] = backend
        if backend == "remote":
            connection = credentials(args.endpoint, args.connection)
            asr_options["endpoint"] = connection["endpoint"]
            if not (work / "transcript.raw.json").exists():
                asr_options["server_model"] = request(connection, "/v1/models")["model"]
            else:
                asr_options["server_model"] = read_json(work / "transcript.raw.json")["model"]
        if imported:
            asr_options["import_sha256"] = file_hash(imported)
        prior = pipeline.data.get("asr_options")
        if prior is not None and prior != asr_options:
            raise ValueError("ASR options changed; use a new session to retain the original transcript")
        pipeline.data["asr_options"] = asr_options

        def asr():
            target = work / "transcript.raw.json"
            if target.exists():
                read_json(target)  # A baseline must be a complete valid document.
            else:
                transcribe(work, backend=backend, model=args.model, language=args.language,
                    multilingual=args.multilingual, device=args.device, compute_type=args.compute_type,
                    import_path=imported, endpoint=args.endpoint, connection=args.connection, timeout=args.timeout,
                    progress=lambda **kw: progress.update("transcribe", **kw), retry_failed=args.retry_failed)
            return ["transcript.raw.json"]

        pipeline.stage("transcribe", {"audio": file_hash(work / "audio.wav"), "options": asr_options}, asr)
        if args.audit:
            pipeline.stage("audit", file_hash(work / "transcript.raw.json"),
                           lambda: (speech_audit(work), ["audit.json"])[1])
        source_state = artifacts(work, ["manifest.json", "visual.json", "transcript.raw.json"] +
                                 (["audit.json"] if (work / "audit.json").exists() else []))
        pipeline.stage("build", source_state, lambda: (build(work), ["timeline.json"])[1])

        def review_bundle():
            request_data = bundle(work)
            return ["review/request.json", "review/instructions.md", "review/response.schema.json"] + request_data["packets"]

        pipeline.stage("review", file_hash(work / "timeline.json"), review_bundle)
        review_files = (sorted((work / "reviews").glob("*.json")) + sorted((work / "decisions").glob("*.json")) +
                        sorted((work / "coverage").glob("*.json")) +
                        ([work / "inspection/evidence.json"] if (work / "inspection/evidence.json").exists() else []))
        export_input = {"timeline": file_hash(work / "timeline.json"),
                        "renderer": {name: file_hash(Path(__file__).with_name(name))
                                     for name in ("export.py", "annotations.py")},
                        "reviews": {str(p.relative_to(work)): file_hash(p) for p in review_files}}
        failures = []
        for fmt in dict.fromkeys(formats):
            def render(fmt=fmt):
                paths = export(work, work / "exports", [fmt])
                if fmt in ("md", "json"):
                    paths += list((work / "exports/frames").glob("*.jpg"))
                return [str(path.relative_to(work)) for path in paths]
            try:
                pipeline.stage("export:" + fmt, export_input, render)
            except (Exception,) as exc:
                failures.append({"format": fmt, "error": str(exc)})
        result = {"work": str(work), "state": "failed" if failures else "completed",
                  "exports": str(work / "exports"), "failures": failures,
                  "review": "bundle_created; LLM review is a separate step"}
        write_json(work / "run-result.json", result)
        return result


def run(args, *, reporter: Progress | None = None) -> dict:
    result = _run_base(args, reporter=reporter)
    if getattr(args, "polish", "auto") == "off":
        return result
    from .polish import automatic_polish
    progress = reporter or Progress(getattr(args, "progress", "text"))
    stopped = threading.Event()
    thread = threading.Thread(target=progress.heartbeat,
        args=("polish", stopped, time.monotonic()), daemon=True)
    progress.emit("started", "polish")
    thread.start()
    try:
        value = getattr(args, "polish_command_json", None) or os.environ.get("PLEASE_TRANSCRIBE_POLISH_COMMAND")
        command = json.loads(value) if value else None
        polishing = automatic_polish(args.work.resolve(), command=command,
            formats=[fmt.strip() for fmt in args.formats.split(",") if fmt.strip()],
            refresh=getattr(args, "polish_refresh", False))
        result["polish"] = polishing
        result["review"] = "automatic_polish_" + polishing["state"]
        if polishing["state"] == "failed":
            result["state"] = "failed"
            result["failures"] += polishing["failures"]
        elif result["state"] != "failed":
            result["state"] = polishing["state"]
        if polishing["state"] == "completed":
            result["polished_exports"] = str(args.work.resolve() / "exports/polished")
            result["primary_exports"] = result["polished_exports"]
        progress.emit(polishing["state"], "polish")
    except Exception as exc:
        result["state"] = "failed"
        result["failures"].append({"stage": "polish", "error": str(exc)})
        progress.emit("failed", "polish")
    finally:
        stopped.set()
        thread.join()
    write_json(args.work.resolve() / "run-result.json", result)
    return result


def batch(args) -> dict:
    from copy import copy
    from .intake import session_name
    sources = list(args.sources)
    if args.input_list:
        sources += [line.strip() for line in args.input_list.read_text(encoding="utf-8-sig").splitlines()
                    if line.strip() and not line.lstrip().startswith("#")]
    if not sources:
        raise ValueError("Supply inputs or --input-list")
    results = []
    for source in dict.fromkeys(sources):
        item = copy(args)
        item.source = source
        item.work = args.output_root / session_name(source)
        try:
            result = run(item)
        except Exception as exc:
            result = {"work": str(item.work), "state": "failed", "error": str(exc)}
        # The stable session name avoids leaking signed URLs into the index.
        result["input_id"] = session_name(source)
        results.append(result)
        write_json(args.output_root / "batch.json", {"items": results})
    index = ["# Batch results", ""]
    for item in results:
        directory = args.output_root / item["input_id"]
        target = next((name for name in ("exports/index.html", "exports/index.md", "exports/transcript.json")
                       if (directory / name).is_file()), None)
        label = f"[{item['input_id']}]({item['input_id']}/{target})" if target else item["input_id"]
        index.append(f"- {label}: {item['state']}")
    args.output_root.mkdir(parents=True, exist_ok=True)
    (args.output_root / "index.md").write_text("\n".join(index) + "\n", encoding="utf-8")
    state = ("failed" if any(r["state"] == "failed" for r in results) else
             "awaiting_polish" if any(r["state"] == "awaiting_polish" for r in results) else "completed")
    return {"items": results, "state": state}
