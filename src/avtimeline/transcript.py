from __future__ import annotations

import platform
import wave
from pathlib import Path
from typing import Any

from .storage import SCHEMA_VERSION, read_json, valid_span, write_json


def read_pcm_audio(path: Path):
    """Prepared WAV is already aligned PCM; avoid engine-specific decoder APIs."""
    import numpy as np
    with wave.open(str(path)) as audio:
        if (audio.getnchannels(), audio.getsampwidth(), audio.getframerate()) != (1, 2, 16000):
            raise ValueError("Expected prepared mono 16 kHz PCM16 audio")
        return np.frombuffer(audio.readframes(audio.getnframes()), dtype="<i2").astype(np.float32) / 32768


def normalize(raw: dict, duration: float, *, backend: str, model: str) -> dict:
    segments = []
    language = raw.get("language")
    for index, segment in enumerate(raw["segments"]):
        start, end = float(segment["start"]), float(segment["end"])
        valid_span(start, end, duration)
        sid = f"s{index:06d}"
        words = []
        for word_index, word in enumerate(segment.get("words") or []):
            ws, we = float(word["start"]), float(word["end"])
            valid_span(ws, we, duration)
            words.append({"id": f"{sid}.w{word_index:04d}", "start": ws, "end": we,
                          "text": word.get("word", word.get("text", "")),
                          "probability": word.get("probability")})
        if not isinstance(segment["text"], str):
            raise ValueError("Segment text must be a string")
        segments.append({"id": sid, "start": start, "end": end, "text": segment["text"],
                         "language": segment.get("language", language), "words": words,
                         "avg_logprob": segment.get("avg_logprob")})
    if any(a["start"] > b["start"] for a, b in zip(segments, segments[1:])):
        raise ValueError("Transcript segments must be in chronological order")
    return {"schema_version": SCHEMA_VERSION, "backend": backend, "model": model,
            "task": "transcribe", "language": language, "segments": segments}


def transcribe(work: Path, *, backend: str | None, model: str | None, language: str,
               multilingual: bool, device: str, compute_type: str,
               import_path: Path | None = None, endpoint: str | None = None,
               connection: Path | None = None, timeout: float = 86400, progress=None,
               retry_failed: bool = False) -> None:
    target = work / "transcript.raw.json"
    if target.exists():
        raise ValueError("transcript.raw.json already exists; use a new work directory to retranscribe")
    manifest = read_json(work / "manifest.json")
    audio = str(work / "audio.wav")
    lang = None if language == "auto" else language
    from .connection import default_config
    import os
    if backend is None:
        backend = "remote" if (endpoint or os.environ.get("PLEASE_TRANSCRIBE_ENDPOINT") or
                               (connection or default_config()).exists()) else "faster-whisper"
    if import_path:
        raw = read_json(import_path)
        model = raw.get("model", "external")
        backend = "import"
    elif backend == "remote":
        from .connection import remote_transcribe
        raw = remote_transcribe(work, endpoint=endpoint, config=connection, model=model,
                               language=language, multilingual=multilingual, timeout=timeout, progress=progress,
                               retry_failed=retry_failed)
        model = raw["model"]
    elif backend == "faster-whisper":
        from faster_whisper import WhisperModel

        model = model or "large-v3"
        engine = WhisperModel(model, device=device, compute_type=compute_type)
        # Do not discard speech based on VAD before generating the baseline.
        generated, info = engine.transcribe(read_pcm_audio(Path(audio)), language=lang, task="transcribe",
                                            word_timestamps=True, vad_filter=False,
                                            multilingual=multilingual)
        segments = []
        for segment in generated:
            if progress:
                progress(position=segment.end, state="transcribing")
            segments.append({"start": segment.start, "end": segment.end, "text": segment.text,
                             "avg_logprob": segment.avg_logprob,
                             "words": [{"start": w.start, "end": w.end, "word": w.word,
                                        "probability": w.probability} for w in segment.words or []]})
        raw = {"language": None if multilingual else info.language, "segments": segments}
    elif backend == "mlx":
        if platform.system() != "Darwin" or platform.machine() != "arm64":
            raise ValueError("The optional MLX backend requires Apple Silicon macOS")
        if multilingual:
            raise ValueError("--multilingual is supported by faster-whisper; MLX supports a selected or auto language")
        import mlx_whisper

        model = model or "mlx-community/whisper-large-v3-mlx"
        raw = mlx_whisper.transcribe(read_pcm_audio(Path(audio)), path_or_hf_repo=model, language=lang,
                                     task="transcribe", word_timestamps=True)
    else:
        raise ValueError(f"Unknown ASR backend: {backend}")
    result = normalize(raw, manifest["duration"], backend=backend, model=model or "external")
    result["multilingual"] = multilingual
    result["language_requested"] = language
    if backend == "remote":
        result["server_backend"] = raw["backend"]
    write_json(target, result, exclusive=True)


def speech_audit(work: Path) -> dict[str, Any]:
    """Independent Silero pass: flag speech not covered by timestamped ASR words."""
    from faster_whisper.vad import get_speech_timestamps, VadOptions

    transcript = read_json(work / "transcript.raw.json")
    audio = read_pcm_audio(work / "audio.wav")
    chunks = get_speech_timestamps(audio, VadOptions(), sampling_rate=16000)
    covered = []
    for segment in transcript["segments"]:
        for item in segment["words"] or [segment]:
            covered.append((max(0, item["start"] - 0.15), item["end"] + 0.15))
    covered.sort()
    gaps = []
    for chunk in chunks:
        start, end = chunk["start"] / 16000, chunk["end"] / 16000
        cursor = start
        for cs, ce in covered:
            if ce <= cursor:
                continue
            if cs >= end:
                break
            if cs - cursor >= 0.3:
                gaps.append({"start": cursor, "end": min(cs, end), "kind": "possible_missing_speech"})
            cursor = max(cursor, ce)
        if end - cursor >= 0.3:
            gaps.append({"start": cursor, "end": end, "kind": "possible_missing_speech"})
    result = {"schema_version": SCHEMA_VERSION, "method": "silero-vad-vs-asr-time-coverage",
              "note": "Candidates only. Coverage does not prove transcription completeness.",
              "candidates": gaps}
    write_json(work / "audit.json", result)
    return result
