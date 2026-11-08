from __future__ import annotations

import argparse
import json
import math
import subprocess
import sys
from pathlib import Path

from . import __version__


def positive(value: str) -> float:
    number = float(value)
    if not math.isfinite(number) or number <= 0:
        raise argparse.ArgumentTypeError("must be finite and greater than zero")
    return number


def nonnegative(value: str) -> float:
    number = float(value)
    if not math.isfinite(number) or number < 0:
        raise argparse.ArgumentTypeError("must be finite and nonnegative")
    return number


def crop_value(value: str) -> tuple[int, int, int, int]:
    try:
        x, y, width, height = (int(part) for part in value.split(","))
    except ValueError as exc:
        raise argparse.ArgumentTypeError("use x,y,width,height") from exc
    if min(x, y) < 0 or min(width, height) <= 0:
        raise argparse.ArgumentTypeError("crop origin must be nonnegative and size positive")
    return x, y, width, height


def add_visual(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--sample-interval", type=positive, default=0.5, help="Seconds between analyzed video samples")
    parser.add_argument("--image-interval", type=positive, default=15, help="Seconds between periodic evidence images")
    parser.add_argument("--change-threshold", type=positive, default=0.08)
    parser.add_argument("--tile-threshold", type=positive, default=0.16)
    parser.add_argument("--min-event-gap", type=nonnegative, default=1)
    parser.add_argument("--crop", type=crop_value, help="Analyze this region; retain full-screen evidence images")
    parser.add_argument("--ocr-languages", help="Optional installed Tesseract packs, e.g. eng+kor+spa")


def add_asr(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--backend", choices=["faster-whisper", "mlx", "remote"],
                        help="Uses configured remote server when connected; otherwise faster-whisper")
    parser.add_argument("--model", help="Backend model ID or local model path; defaults to large-v3")
    parser.add_argument("--language", default="auto", help="auto, en, ko, es, or another Whisper language code")
    parser.add_argument("--multilingual", action="store_true", help="Detect language per decoding window (faster-whisper)")
    parser.add_argument("--device", choices=["cpu", "cuda", "auto"], default="cpu")
    parser.add_argument("--compute-type", default="int8", help="faster-whisper compute type")
    parser.add_argument("--import-transcript", type=Path, help="Import timestamped ASR JSON from another engine")
    parser.add_argument("--endpoint", help="Remote transcription API URL")
    parser.add_argument("--connection", type=Path, help="Private client connection profile")
    parser.add_argument("--timeout", type=positive, default=86400, help="Remote job polling timeout in seconds")
    parser.add_argument("--retry-failed", action="store_true", help="Retry a failed remote job once, preserving attempt history")


def add_ssh(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--ssh", help="Mac server SSH target, e.g. user@server; omit to install on this Mac")
    parser.add_argument("--ssh-port", type=int, default=22)
    parser.add_argument("--identity", type=Path, help="SSH private key path; passwords use SSH's interactive prompt")
    parser.add_argument("--ssh-known-hosts", type=Path)
    parser.add_argument("--ssh-control-path", help="Optional existing SSH ControlPath")
    parser.add_argument("--state-dir", default="~/.local/share/please-transcribe")


def parser() -> argparse.ArgumentParser:
    root = argparse.ArgumentParser(prog="please-transcribe", description="Multilingual audio/video evidence timeline")
    root.add_argument("--version", action="version", version=__version__)
    commands = root.add_subparsers(dest="command", required=True)
    connect = commands.add_parser("connect", help="Save and verify a private remote-server profile")
    connect.add_argument("--endpoint", required=True)
    connect.add_argument("--api-key-file", type=Path, help="Otherwise read PLEASE_TRANSCRIBE_API_KEY or prompt")
    connect.add_argument("--config", type=Path)
    doctor = commands.add_parser("doctor", help="Check configured server authentication and model state")
    doctor.add_argument("--endpoint")
    doctor.add_argument("--connection", type=Path)
    doctor.add_argument("--backend", choices=["faster-whisper", "mlx", "remote"])
    doctor.add_argument("--pdf", action="store_true")
    doctor.add_argument("--url", action="store_true")
    doctor.add_argument("--server-only", action="store_true")
    server = commands.add_parser("server", help="Explicit Mac server installation and lifecycle control")
    server_actions = server.add_subparsers(dest="action", required=True)
    install = server_actions.add_parser("install")
    add_ssh(install)
    install.add_argument("--backend", choices=["mlx", "faster-whisper"], default="mlx")
    install.add_argument("--model")
    install.add_argument("--model-source", choices=["auto", "openai", "huggingface"], default="auto")
    install.add_argument("--port", type=int, default=8765)
    install.add_argument("--bind-address", default="127.0.0.1")
    install.add_argument("--network", help="Existing Docker network to attach the API to")
    install.add_argument("--client-name", default="default", help="User identity for an independent API key")
    install.add_argument("--endpoint", help="URL that the installing client will use")
    install.add_argument("--config", type=Path, help="Private connection profile to write on the client")
    install.add_argument("--wait-timeout", type=positive, default=900, help="Seconds to wait for initial model download/loading")
    for action in ("status", "start", "stop"):
        sub = server_actions.add_parser(action)
        add_ssh(sub)
    prepare = commands.add_parser("prepare", help="Extract aligned audio and timestamped screen evidence")
    prepare.add_argument("source", type=Path)
    prepare.add_argument("--work", type=Path, required=True)
    add_visual(prepare)
    transcribe = commands.add_parser("transcribe", help="Create an immutable original-language transcript")
    transcribe.add_argument("--work", type=Path, required=True)
    add_asr(transcribe)
    audit = commands.add_parser("audit", help="Find VAD speech without ASR timestamp coverage")
    audit.add_argument("--work", type=Path, required=True)
    build = commands.add_parser("build", help="Link screen observations and speech by time")
    build.add_argument("--work", type=Path, required=True)
    build.add_argument("--context", type=nonnegative, default=5)
    review = commands.add_parser("review", help="Portable file-based agent review")
    actions = review.add_subparsers(dest="action", required=True)
    bundle = actions.add_parser("bundle")
    bundle.add_argument("--work", type=Path, required=True)
    bundle.add_argument("--window", type=positive, default=60)
    bundle.add_argument("--context", type=nonnegative, default=5)
    imp = actions.add_parser("import")
    imp.add_argument("response", type=Path)
    imp.add_argument("--work", type=Path, required=True)
    run_review = actions.add_parser("run")
    run_review.add_argument("--work", type=Path, required=True)
    run_review.add_argument("--command-json", required=True, help="JSON argv array of your own harness wrapper; no shell")
    run_review.add_argument("--timeout", type=positive, default=600)
    focus = actions.add_parser("focus", help="Create an audio/image review packet for uncertain speech")
    focus.add_argument("--work", type=Path, required=True)
    focus.add_argument("--limit", type=int, default=20)
    focus.add_argument("--offset", type=int, default=0)
    status = actions.add_parser("status", help="Explicit review coverage and pending candidates")
    status.add_argument("--work", type=Path, required=True)
    decision = actions.add_parser("decide", help="Append an accepted/rejected/deferred candidate decision")
    decision.add_argument("candidate_id")
    decision.add_argument("--work", type=Path, required=True)
    decision.add_argument("--decision", choices=["accepted", "rejected", "deferred"], required=True)
    decision.add_argument("--actor", required=True)
    decision.add_argument("--reason", required=True)
    decision.add_argument("--audio-inspected", action="store_true")
    coverage = actions.add_parser("coverage", help="Attest which segments were actually inspected")
    coverage.add_argument("segment_ids", nargs="+")
    coverage.add_argument("--work", type=Path, required=True)
    coverage.add_argument("--reviewer", required=True)
    coverage.add_argument("--audio-inspected", action="store_true")
    coverage.add_argument("--images-inspected", action="store_true")
    inspect = commands.add_parser("inspect", help="Investigate a bounded part of the recording")
    inspect_actions = inspect.add_subparsers(dest="action", required=True)
    for name in ("context", "audio", "frames"):
        item = inspect_actions.add_parser(name)
        item.add_argument("--work", type=Path, required=True)
        item.add_argument("--start", type=nonnegative, required=True)
        item.add_argument("--end", type=nonnegative, required=True)
        if name == "context":
            item.add_argument("--context", type=nonnegative, default=3)
            item.add_argument("--max-frames", type=int, default=12)
            item.add_argument("--max-segments", type=int, default=100)
            item.add_argument("--offset", type=int, default=0)
        elif name == "frames":
            item.add_argument("--source", type=Path)
            item.add_argument("--interval", type=positive, default=0.5)
            item.add_argument("--max-frames", type=int, default=60)
    mcp = commands.add_parser("mcp", help="Serve agent investigation tools over MCP stdio")
    mcp.add_argument("--root", type=Path, required=True, help="Only sessions inside this directory are accessible")
    export = commands.add_parser("export", help="Export md, html, pdf, json, srt, vtt")
    export.add_argument("--work", type=Path, required=True)
    export.add_argument("--output", type=Path, required=True)
    export.add_argument("--formats", default="md,html,json,srt,vtt")
    run = commands.add_parser("run", help="Prepare, transcribe, build, bundle and export")
    run.add_argument("source", help="Local recording or http(s) video URL")
    run.add_argument("--work", type=Path, required=True)
    run.add_argument("--formats", default="md,html,json,srt,vtt")
    run.add_argument("--audit", action="store_true")
    run.add_argument("--progress", choices=["text", "json", "quiet"], default="text")
    add_visual(run)
    add_asr(run)
    batch = commands.add_parser("batch", help="Process local recordings and/or URLs independently; resume automatically")
    batch.add_argument("sources", nargs="*")
    batch.add_argument("--input-list", type=Path, help="UTF-8 text file, one path or URL per line")
    batch.add_argument("--output-root", type=Path, required=True)
    batch.add_argument("--formats", default="md,html,json,srt,vtt")
    batch.add_argument("--audit", action="store_true")
    batch.add_argument("--progress", choices=["text", "json", "quiet"], default="text")
    add_visual(batch)
    add_asr(batch)
    return root


def execute(args: argparse.Namespace) -> None:
    from .export import export
    from .media import prepare
    from .review import bundle, import_response, run_command
    from .storage import read_json
    from .timeline import build
    from .transcript import speech_audit, transcribe
    from .connection import credentials, request, save_connection

    def prep() -> None:
        names = ("sample_interval", "image_interval", "change_threshold", "tile_threshold",
                 "min_event_gap", "crop", "ocr_languages")
        prepare(args.source, args.work, **{name: getattr(args, name) for name in names})

    def asr() -> None:
        names = ("backend", "model", "language", "multilingual", "device", "compute_type", "endpoint", "connection", "timeout", "retry_failed")
        transcribe(args.work, **{name: getattr(args, name) for name in names}, import_path=args.import_transcript)

    if args.command == "connect":
        import getpass
        import os
        key = (args.api_key_file.read_text().strip() if args.api_key_file else
               os.environ.get("PLEASE_TRANSCRIBE_API_KEY") or getpass.getpass("Transcription API key: "))
        print("Connection saved:", save_connection({"endpoint": args.endpoint.rstrip("/"), "api_key": key}, args.config))
    elif args.command == "doctor":
        from .diagnostics import diagnose
        report = diagnose(endpoint=args.endpoint, connection=args.connection, backend=args.backend,
                          pdf=args.pdf, url=args.url, server_only=args.server_only)
        print(json.dumps(report, ensure_ascii=False, indent=2))
        if not report["okay"]:
            raise SystemExit(1)
    elif args.command == "server":
        from .server_install import install, lifecycle
        if args.action == "install":
            print("Server configured. Private client profile:", install(args))
            print("Run doctor to check model loading and endpoint reachability.")
        else:
            lifecycle(args)
    elif args.command == "prepare":
        prep()
    elif args.command == "transcribe":
        asr()
    elif args.command == "audit":
        speech_audit(args.work)
        print("Audit saved. Run build, then regenerate review bundles and exports.")
    elif args.command == "build":
        build(args.work, context=args.context)
    elif args.command == "review":
        if args.action == "bundle":
            bundle(args.work, window=args.window, context=args.context)
        elif args.action == "import":
            print(import_response(args.work, read_json(args.response)))
        elif args.action == "run":
            print(run_command(args.work, json.loads(args.command_json), timeout=args.timeout))
        else:
            from .triage import focus, status, decide, mark_coverage
            if args.action == "focus":
                result = focus(args.work, limit=args.limit, offset=args.offset)
            elif args.action == "status":
                result = status(args.work)
            elif args.action == "decide":
                result = decide(args.work, args.candidate_id, args.decision, args.actor, args.reason,
                                audio_inspected=args.audio_inspected)
            else:
                result = mark_coverage(args.work, args.segment_ids, args.reviewer,
                    audio_inspected=args.audio_inspected, images_inspected=args.images_inspected)
            print(json.dumps(result, ensure_ascii=False, indent=2))
    elif args.command == "inspect":
        from .evidence import get_context, clip_audio, dense_frames
        if args.action == "context":
            result = get_context(args.work, args.start, args.end, context=args.context,
                                 max_frames=args.max_frames, max_segments=args.max_segments, offset=args.offset)
        elif args.action == "audio":
            result = clip_audio(args.work, args.start, args.end)
        else:
            result = dense_frames(args.work, args.start, args.end, interval=args.interval,
                                  max_frames=args.max_frames, source=args.source)
        print(json.dumps(result, ensure_ascii=False, indent=2))
    elif args.command == "mcp":
        from .mcp_server import serve
        serve(args.root)
    elif args.command == "export":
        for path in export(args.work, args.output, args.formats.split(",")):
            print(path)
    elif args.command == "run":
        from .pipeline import run
        result = run(args)
        print(json.dumps(result, ensure_ascii=False, indent=2))
        if result["state"] == "failed":
            raise SystemExit(1)
    elif args.command == "batch":
        from .pipeline import batch
        result = batch(args)
        print(json.dumps(result, ensure_ascii=False, indent=2))
        if result["state"] == "failed":
            raise SystemExit(1)


def main() -> None:
    args = parser().parse_args()
    try:
        execute(args)
    except subprocess.CalledProcessError as exc:
        print(f"avtimeline: {exc.cmd[0]} exited with status {exc.returncode}; see the error above", file=sys.stderr)
        sys.exit(1)
    except (ValueError, OSError, ImportError, subprocess.SubprocessError) as exc:
        print(f"avtimeline: {exc}", file=sys.stderr)
        sys.exit(1)
