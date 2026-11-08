"""Validate distributable demo evidence without downloading models or using keys."""
from __future__ import annotations

import base64
import hashlib
from html.parser import HTMLParser
import json
from pathlib import Path
import re


class Document(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.speech, self.ids, self.images, self.audio = {}, [], [], []
        self.segment, self.first, self.in_p = None, False, False

    def handle_starttag(self, tag, attributes):
        attrs = dict(attributes)
        assert tag not in {"style", "script", "link", "iframe"}, f"Unexpected HTML component: {tag}"
        assert not any(key in {"style", "class"} or key.startswith("on") for key in attrs), "Styled or scripted HTML"
        if tag == "section" and attrs.get("id", "").startswith("s"):
            self.segment = attrs["id"]
            self.ids.append(self.segment)
            self.speech[self.segment] = ""
            self.first = True
        if tag == "p" and self.segment and self.first:
            self.in_p = True
        if tag == "img":
            assert attrs["src"].startswith("data:image/jpeg;base64,"), "External or invented image source"
            image = base64.b64decode(attrs["src"].split(",", 1)[1])
            assert image.startswith(b"\xff\xd8"), "Invalid JPEG payload"
            self.images.append(image)
        if tag == "audio":
            audio = base64.b64decode(attrs["src"].split(",", 1)[1])
            assert audio.startswith(b"RIFF") and audio[8:12] == b"WAVE", "Invalid WAV excerpt"
            self.audio.append(audio)

    def handle_endtag(self, tag):
        if tag == "p" and self.in_p:
            self.in_p, self.first = False, False
        if tag == "section":
            self.segment = None

    def handle_data(self, text):
        if self.segment and self.in_p:
            self.speech[self.segment] += text


def main():
    root = Path(__file__).resolve().parents[1] / "examples/demo"
    for name in ("index.md", "index.html", "index.pdf", "transcript.json", "transcript.srt", "transcript.vtt"):
        assert (root / name).is_file() and (root / name).stat().st_size > 0, f"Missing output: {name}"
    data = json.loads((root / "transcript.json").read_text(encoding="utf-8"))
    segments = data["transcript"]["segments"]
    page = Document()
    page.feed((root / "index.html").read_text(encoding="utf-8"))
    assert page.ids == [s["id"] for s in segments], "Lost, duplicated, or reordered speech"
    assert page.speech == {s["id"]: s["text"] for s in segments}, "Original speech was rewritten"
    source_hash = hashlib.sha256((root / "source.mp4").read_bytes()).hexdigest()
    assert source_hash == data["manifest"]["source"]["sha256"], "Demo recording differs from the processed input"
    assert (root / "index.pdf").read_bytes().startswith(b"%PDF-"), "Missing PDF"
    for frame in data["visual"]["frames"] + data.get("inspection", []):
        image = (root / frame["image"]).read_bytes()
        assert image in page.images, f"Screen image is missing from standalone HTML: {frame['id']}"
    for link in re.findall(r'!\[[^]]*\]\(([^)]+)\)', (root / "index.md").read_text(encoding="utf-8")):
        assert (root / link).is_file(), f"Missing Markdown image: {link}"
    for segment in segments:
        assert segment["text"].strip() in (root / "transcript.srt").read_text(encoding="utf-8")
        assert segment["text"].strip() in (root / "transcript.vtt").read_text(encoding="utf-8")
    assert (root / "transcript.vtt").read_text(encoding="utf-8").startswith("WEBVTT")
    queue = Document()
    queue.feed((root / "review.html").read_text(encoding="utf-8"))
    assert queue.audio, "Review demo lacks actual audio"
    assert len(data["visual"]["events"]) >= 2, "Missing screen transitions"
    assert data["decisions"] and data["review_status"]["audio_reviewed"] == 0, "Review provenance changed"
    public_json = (root / "transcript.json").read_text(encoding="utf-8")
    for private in ('/Users/', '/workspace/', '/home/', 'api_key', 'Bearer ', '10.19.'):
        assert private not in public_json, f"Private runtime data in distributable demo: {private}"
    print(f"Demo verified: {len(segments)} original speech segments, {len(page.images)} extracted images, "
          f"{len(queue.audio)} playable excerpts, six formats, no CSS/JS or private runtime paths.")


if __name__ == "__main__":
    main()
