"""Build an owned, reproducible demo from real Python outputs and narration.

The recording is controlled/synthetic, not a capture of a user's lecture. On a Mac
the default narration is generated with say. Else provide three audio files.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import shutil
import subprocess
import sys

from PIL import Image, ImageDraw, ImageFont

PHASES = [
    {"title": "1. Investigate the error", "code": "print(result)",
     "narration": "This Python program prints result before defining it. The traceback shows a NameError."},
    {"title": "2. Define the missing variable", "code": "result = 6 * 7\nprint(result)",
     "narration": "Now result is six times seven. Running the program prints forty-two."},
    {"title": "3. Keep the original languages", "code": "print('안녕하세요 / Hola / Hello')",
     "narration": "Finally the program prints greetings in Korean, Spanish, and English."},
]


def font(size):
    for path in ("/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
                 "/System/Library/Fonts/ヒラギノ角ゴシック W3.ttc",
                 "/usr/share/fonts/truetype/dejavu/DejaVuSansMono.ttf"):
        if Path(path).exists():
            return ImageFont.truetype(path, size)
    return ImageFont.load_default(size=size)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--narration", type=Path, nargs=3)
    parser.add_argument("--voice", default="Samantha")
    args = parser.parse_args()
    if not shutil.which("ffmpeg") or not shutil.which("ffprobe"):
        raise SystemExit("Install FFmpeg/ffprobe before generating the demo")
    args.output.mkdir(parents=True, exist_ok=True)
    records = []
    cursor = 0
    for index, phase in enumerate(PHASES):
        audio = args.narration[index] if args.narration else args.output / f"narration-{index}.aiff"
        if not args.narration:
            if not shutil.which("say"):
                raise SystemExit("Provide --narration with three audio files, or run with macOS say installed")
            subprocess.run(["say", "-v", args.voice, "-o", str(audio), phase["narration"]], check=True)
        duration = float(subprocess.check_output([
            "ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "default=nw=1:nk=1", str(audio)], text=True))
        executed = subprocess.run([sys.executable, "-c", phase["code"]], text=True, capture_output=True)
        output = executed.stdout + executed.stderr
        image = Image.new("RGB", (960, 540), "white")
        draw = ImageDraw.Draw(image)
        draw.text((24, 18), phase["title"], font=font(28), fill="black")
        draw.line((24, 62, 936, 62), fill="gray", width=2)
        draw.text((24, 80), "Python source", font=font(20), fill="black")
        draw.multiline_text((24, 115), phase["code"], font=font(24), fill="black", spacing=10)
        draw.text((24, 225), "Actual process output", font=font(20), fill="black")
        draw.multiline_text((24, 265), output.strip(), font=font(21), fill="black", spacing=7)
        draw.text((24, 495), "Controlled demo · synthesized narration · actual Python execution", font=font(16), fill="black")
        frame = args.output / f"phase-{index}.png"
        image.save(frame)
        records.append({**phase, "start": cursor, "end": cursor + duration,
                        "process_output": output, "exit_code": executed.returncode,
                        "image": frame.name, "audio": str(audio.resolve())})
        cursor += duration
    listing = args.output / "frames.txt"
    listing.write_text("".join(f"file 'phase-{i}.png'\nduration {r['end'] - r['start']}\n" for i, r in enumerate(records))
                       + "file 'phase-2.png'\n", encoding="utf-8")
    command = ["ffmpeg", "-v", "error", "-y", "-f", "concat", "-safe", "0", "-i", str(listing)]
    for record in records:
        command += ["-i", record["audio"]]
    command += ["-filter_complex", "[1:a][2:a][3:a]concat=n=3:v=0:a=1[a]", "-map", "0:v", "-map", "[a]",
                "-r", "10", "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac", "-shortest", str(args.output / "source.mp4")]
    subprocess.run(command, check=True)
    for record in records:
        record.pop("audio")  # No absolute machine paths in distributable metadata.
    (args.output / "ground-truth.json").write_text(json.dumps({
        "kind": "controlled synthetic demo", "narration_source": "TTS or supplied recording",
        "phases": records, "note": "Narration text is the synthesis script, not a human-audited transcript."},
        ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(args.output / "source.mp4")


if __name__ == "__main__":
    main()
