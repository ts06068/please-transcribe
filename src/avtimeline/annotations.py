"""Raw-text tooltips and PDF comments located by the browser's printed links."""
from __future__ import annotations

from difflib import SequenceMatcher
import html
from pathlib import Path
import re

from .storage import fingerprint

PDF_LINK_PREFIX = "https://please-transcribe.invalid/raw/"


def annotations(data: dict, queue: list[dict]) -> list[dict]:
    flagged = {sid for candidate in queue for sid in candidate["segment_ids"]}
    result = []
    for segment in data["transcript"]["segments"]:
        text, raw = segment["text"], segment.get("original_text", segment["text"])
        spans = []
        if text != raw:
            old, new = list(re.finditer(r"\S+", raw)), list(re.finditer(r"\S+", text))
            matcher = SequenceMatcher(None, [m.group() for m in old], [m.group() for m in new], autojunk=False)
            for kind, a, b, c, d in matcher.get_opcodes():
                if kind == "equal":
                    continue
                original = raw[old[a].start():old[b - 1].end()] if a < b else ""
                if c == d and new:  # A deletion is attached to its surviving neighbor.
                    c, d = min(c, len(new) - 1), min(c, len(new) - 1) + 1
                if c < d:
                    spans.append((new[c].start(), new[d - 1].end(), original, "corrected"))
        elif segment["id"] in flagged and text.strip():
            spans.append((0, len(text), raw, "uncertain"))
        # Overlapping deletion/replacement spans are represented once.
        merged = []
        for start, end, original, status in sorted(spans):
            if merged and start < merged[-1][1]:
                prior = merged.pop()
                merged.append((prior[0], max(end, prior[1]), raw, status))
            else:
                merged.append((start, end, original, status))
        for start, end, original, status in merged:
            item = {"segment_id": segment["id"], "start": start, "end": end,
                    "raw_text": original, "status": status}
            item["id"] = "a" + fingerprint(item)[:20]
            result.append(item)
    return result


def speech_html(segment: dict, items: list[dict], *, pdf_links: bool = False) -> str:
    text, cursor, parts = segment["text"], 0, []
    for item in items:
        start, end = item["start"], item["end"]
        parts.append(html.escape(text[cursor:start]))
        raw = item["raw_text"] or "[no corresponding text in raw ASR]"
        title = html.escape("Raw text: " + raw, quote=True).replace("\n", "&#10;").replace("\r", "&#13;")
        content = html.escape(text[start:end])
        marked = f'<mark title="{title}">{content}</mark>'
        if pdf_links:
            # Only the private print intermediate uses this link styling. The
            # actual HTML export has native mark/title elements and no CSS/JS.
            marked = (f'<a href="{PDF_LINK_PREFIX}{item["id"]}" '
                      f'style="color:inherit;text-decoration:none">{marked}</a>')
        parts.append(marked)
        cursor = end
    parts.append(html.escape(text[cursor:]))
    return "".join(parts).replace("\n", "<br>")


def annotate_pdf(source: bytes, items: list[dict], output: Path) -> None:
    import pymupdf

    by_id = {item["id"]: item for item in items}
    found = set()
    with pymupdf.open(stream=source, filetype="pdf") as document:
        for page in document:
            groups = {}
            for link in page.get_links():
                uri = link.get("uri", "")
                if not uri.startswith(PDF_LINK_PREFIX):
                    continue
                identity = uri[len(PDF_LINK_PREFIX):]
                if identity not in by_id:
                    raise ValueError("Unknown raw-text annotation in printed PDF")
                groups.setdefault(identity, []).append(pymupdf.Rect(link["from"]))
                page.delete_link(link)
            for identity, rectangles in groups.items():
                item = by_id[identity]
                quads = [pymupdf.Quad(r.tl, r.tr, r.bl, r.br) for r in rectangles]
                annotation = page.add_highlight_annot(quads)
                annotation.set_colors(stroke=(1, 1, 0))
                annotation.set_info(title="please-transcribe", subject=item["segment_id"] + " · " + item["status"],
                                    content="Raw text: " + (item["raw_text"] or "[no corresponding text in raw ASR]"))
                annotation.set_popup(pymupdf.Rect(20, 20, 300, 140))
                annotation.set_open(False)
                annotation.update()
                found.add(identity)
        if found != set(by_id):
            raise ValueError("Some uncertain speech could not be located in the printed PDF")
        document.save(str(output), garbage=4, deflate=True)
