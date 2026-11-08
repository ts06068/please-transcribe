from __future__ import annotations

import json
import math
import shutil
import subprocess
import wave
from pathlib import Path

from .storage import SCHEMA_VERSION, file_hash, write_json


def executable(name: str) -> str:
    found = shutil.which(name)
    if not found:
        raise ValueError(f"{name} is required. Install FFmpeg and add its bin directory to PATH.")
    return found


def probe(source: Path) -> dict:
    result = subprocess.run([executable("ffprobe"), "-v", "error", "-show_format", "-show_streams",
                             "-of", "json", str(source.resolve())], check=True, capture_output=True, text=True)
    data = json.loads(result.stdout)
    video = next((s for s in data["streams"] if s["codec_type"] == "video"
                  and not s.get("disposition", {}).get("attached_pic")), None)
    audio = next((s for s in data["streams"] if s["codec_type"] == "audio"), None)
    if not video or not audio:
        raise ValueError("Input must contain a video stream and an audio stream")
    origin = min(float(s.get("start_time", data["format"].get("start_time", 0))) for s in (video, audio))
    ends = [float(s.get("start_time", origin)) + float(s["duration"])
            for s in (video, audio) if s.get("duration") not in (None, "N/A")]
    if len(ends) == 2:
        duration = max(ends) - origin
    else:
        duration = float(data["format"]["duration"])
    if not math.isfinite(duration) or duration <= 0 or not math.isfinite(origin):
        raise ValueError("Input needs a finite positive duration and valid stream timestamps")
    return {"duration": duration, "origin": origin,
            "video_stream_index": video["index"], "audio_stream_index": audio["index"]}


def extract_audio(source: Path, work: Path, metadata: dict) -> None:
    # Preserve the offset between streams. Resetting each stream independently
    # would move speech onto the wrong images when audio starts later than video.
    origin = metadata["origin"]
    subprocess.run([
        executable("ffmpeg"), "-v", "error", "-nostdin", "-y", "-copyts", "-i", str(source.resolve()),
        "-map", f"0:{metadata['audio_stream_index']}", "-vn",
        "-af", f"asetpts=PTS-({origin})/TB,aresample=16000:async=1:first_pts=0",
        "-ac", "1", "-ar", "16000",
        "-c:a", "pcm_s16le", str(work / "audio.wav"),
    ], check=True)


def extract_visuals(source: Path, work: Path, metadata: dict, *, sample_interval: float,
                    image_interval: float, change_threshold: float, tile_threshold: float,
                    min_event_gap: float, crop: tuple[int, int, int, int] | None,
                    ocr_languages: str | None, progress=None) -> dict:
    import av
    import numpy as np

    frames_dir = work / "frames"
    frames_dir.mkdir()
    frames, events, saved = [], [], {}
    previous, previous_gray = None, None
    pending = []
    next_sample, next_image, last_event = 0.0, 0.0, -math.inf
    missing_pts = 0
    last_time = -math.inf
    video_end = 0.0
    estimated_frame_durations = 0

    def save(item: tuple[float, object], reason: str) -> str:
        t, image = item
        if t in saved:
            entry = saved[t]
            if reason not in entry["reasons"]:
                entry["reasons"].append(reason)
            return entry["id"]
        fid = f"f{len(saved):06d}"
        relative = f"frames/{fid}.jpg"
        image.save(work / relative, quality=88)
        entry = {"id": fid, "time": round(t, 6), "image": relative, "reasons": [reason]}
        if ocr_languages:
            result = subprocess.run([executable("tesseract"), str(work / relative), "stdout",
                                     "-l", ocr_languages], check=True, capture_output=True, text=True,
                                    encoding="utf-8")
            entry["ocr"] = result.stdout.strip()
        saved[t] = entry
        frames.append(entry)
        return fid

    with av.open(str(source)) as container:
        stream = next(s for s in container.streams.video if s.index == metadata["video_stream_index"])
        for frame in container.decode(stream):
            if frame.pts is None or not frame.time_base:
                missing_pts += 1
                continue
            t = float(frame.pts * frame.time_base) - metadata["origin"]
            frame_duration = getattr(frame, "duration", 0)
            if frame_duration:
                video_end = max(video_end, t + float(frame_duration * frame.time_base))
            else:
                estimated_frame_durations += 1
                duration_hint = float(1 / stream.average_rate) if stream.average_rate else sample_interval
                video_end = max(video_end, t + duration_hint)
            if t < 0 or t <= last_time:
                continue
            last_time = t
            if t + 1e-6 < next_sample:
                continue
            next_sample = t + sample_interval
            if progress:
                progress(position=round(t, 3), estimated_duration=metadata["duration"],
                         images=len(frames), changes=len(events))
            image = frame.to_image().convert("RGB")
            region = image
            if crop:
                x, y, width, height = crop
                if min(x, y) < 0 or min(width, height) <= 0 or x + width > image.width or y + height > image.height:
                    raise ValueError("--crop x,y,width,height must fit inside the video")
                region = image.crop((x, y, x + width, y + height))
            gray = np.asarray(region.convert("L").resize((192, 108)), dtype=np.float32) / 255
            image.thumbnail((1280, 1280))
            current = (t, image)
            if pending:
                after = save(current, "after-change")
                for event in pending:
                    event["frame_ids"].append(after)
                pending = []
            if t >= next_image:
                save(current, "periodic")
                next_image = t + image_interval
            if previous_gray is not None:
                diff = np.abs(gray - previous_gray)
                score = float(diff.mean())
                tile_score = float(diff.reshape(6, 18, 8, 24).mean(axis=(1, 3)).max())
                if (score >= change_threshold or tile_score >= tile_threshold) and t - last_event >= min_event_gap:
                    before = save(previous, "before-change")
                    at = save(current, "at-change")
                    event = {"id": f"e{len(events):06d}", "kind": "visual_change_candidate",
                             "time": round(t, 6), "observed_between": [previous[0], t],
                             "score": round(score, 6), "tile_score": round(tile_score, 6),
                             "frame_ids": [before, at]}
                    events.append(event)
                    pending.append(event)
                    last_event = t
            previous, previous_gray = current, gray
    if previous is None:
        raise ValueError("No timestamped video frames could be decoded")
    save(previous, "last-sample")
    frames.sort(key=lambda item: item["time"])
    result = {"schema_version": SCHEMA_VERSION, "method": "sampled-global-and-tile-pixel-difference",
              "parameters": {"sample_interval": sample_interval, "image_interval": image_interval,
                             "change_threshold": change_threshold, "tile_threshold": tile_threshold,
                             "min_event_gap": min_event_gap, "crop": crop},
              "missing_pts_frames": missing_pts, "frames": frames, "events": events,
              "observed_video_end": video_end,
              "estimated_frame_durations": estimated_frame_durations,
              "note": "Visual changes are candidates, not semantic scene labels. Brief changes between samples may be missed."}
    return result


def prepare(source: Path, work: Path, *, progress=None, **visual_options: object) -> None:
    if work.exists() and any(work.iterdir()):
        raise ValueError("prepare requires an empty work directory; existing runs are preserved")
    metadata = probe(source)
    # Check imports before creating partial output.
    import av  # noqa: F401
    import numpy  # noqa: F401
    from PIL import Image  # noqa: F401

    work.mkdir(parents=True, exist_ok=True)
    extract_audio(source, work, metadata)
    visual = extract_visuals(source, work, metadata, progress=progress, **visual_options)
    with wave.open(str(work / "audio.wav")) as audio:
        audio_end = audio.getnframes() / audio.getframerate()
    # Container duration metadata differs across formats with nonzero origins.
    # Derive bounds from decoded video timestamps and actual aligned audio samples,
    # rather than trimming speech to an uncertain metadata duration.
    duration = max(audio_end, visual["observed_video_end"])
    write_json(work / "visual.json", visual, exclusive=True)
    write_json(work / "manifest.json", {
        "schema_version": SCHEMA_VERSION, "source": {"name": source.name, "sha256": file_hash(source)},
        "duration": duration, "source_time_origin": metadata["origin"],
        "timeline": "seconds from the earliest selected audio/video stream start",
        "audio": "audio.wav", "video_stream_index": metadata["video_stream_index"],
        "audio_stream_index": metadata["audio_stream_index"],
    }, exclusive=True)
    write_json(work / "source.json", {"path": str(source.resolve()), "sha256": file_hash(source)}, exclusive=True)
