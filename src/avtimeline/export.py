from __future__ import annotations

import base64
import html
import re
import shutil
from pathlib import Path

from .review import load_reviews
from .storage import asset_path, fingerprint, read_json, timestamp, write_json

FORMATS = {"md", "html", "pdf", "json", "srt", "vtt"}


def md_text(text: str) -> str:
    text = html.escape(text, quote=False)
    return re.sub(r"([\\`*_{}\[\]()#+.!|>~-])", r"\\\1", text).replace("\n", "  \n")


def document_items(data: dict):
    # Each original segment is emitted exactly once, even across visual changes.
    items = [(s["start"], 2, "segment", s) for s in data["transcript"]["segments"]]
    items += [(f["time"], 0, "frame", f) for f in data["visual"]["frames"] + data.get("inspection", [])]
    items += [(e["time"], 1, "event", e) for e in data["visual"]["events"]]
    return sorted(items, key=lambda item: (item[0], item[1]))


def render_html(data: dict, reviews: list[dict], work: Path) -> str:
    esc = html.escape
    title = esc(data["manifest"]["source"]["name"])
    chunks = ["<!doctype html>", '<html lang="und">', '<head><meta charset="utf-8">',
              f"<title>{title}</title></head>", f"<body><h1>{title}</h1>",
              "<p>Original-language transcript and timestamped screen observations. "
              "Visual changes and temporal links are candidates for review.</p>",
              f'<p>Source time origin: {data["manifest"]["source_time_origin"]} seconds. '
              'Displayed times are relative to this origin.</p>']
    link_map = {link["segment_id"]: link for link in data["links"]}
    for _, _, kind, item in document_items(data):
        if kind == "segment":
            lang = esc(item.get("language") or "und", quote=True)
            text = esc(item["text"]).replace("\n", "<br>")
            chunks += [f'<section id="{esc(item["id"])}"><h2>{timestamp(item["start"])}–'
                       f'{timestamp(item["end"])}</h2>', f'<p lang="{lang}" dir="auto">{text}</p>']
            frames = link_map[item["id"]]["frame_ids"]
            if frames:
                links = " ".join(f'<a href="#{esc(fid)}">{esc(fid)}</a>' for fid in frames)
                chunks.append(f"<p>Screen context: {links}</p>")
            chunks.append("</section>")
        elif kind == "frame":
            image = base64.b64encode(asset_path(work, item["image"]).read_bytes()).decode("ascii")
            caption = f'Screen at {timestamp(item["time"])} ({", ".join(item["reasons"])})'
            chunks += [f'<figure id="{esc(item["id"])}">',
                       f'<img src="data:image/jpeg;base64,{image}" width="600" alt="{esc(caption)}">',
                       f"<figcaption>{esc(caption)}</figcaption></figure>"]
            if item.get("ocr"):
                chunks.append(f'<details><summary>OCR</summary><pre>{esc(item["ocr"])}</pre></details>')
        else:
            lo, hi = item["observed_between"]
            chunks.append(f'<p id="{esc(item["id"])}">Visual change candidate '
                          f'{esc(item["id"])}: observed between {timestamp(lo)} and {timestamp(hi)}.</p>')
    audit = data.get("audit")
    if audit:
        chunks.append("<h2>Speech coverage audit</h2><p>These are candidates, not a completeness guarantee.</p><ul>")
        chunks.extend(f'<li>{timestamp(c["start"])}–{timestamp(c["end"])}: possible missing speech</li>'
                      for c in audit["candidates"])
        chunks.append("</ul>")
    for review in reviews:
        chunks.append(f'<h2>Review: {esc(review["reviewer"])}</h2><p>Suggestions; original speech is preserved above.</p>')
        for index, correction in enumerate(review["corrections"]):
            pid = "p" + fingerprint({"report": fingerprint(review), "index": index})[:20]
            history = [d for d in data.get("decisions", []) if d.get("proposal_id") == pid]
            decision = sorted(history, key=lambda d: d["created"])[-1] if history else None
            chunks.append(f'<p><a href="#{esc(correction["segment_id"])}">{esc(correction["segment_id"])}</a>: '
                          f'{esc(correction["original_text"])} → {esc(correction["proposed_text"])}<br>'
                          f'Reason: {esc(correction["reason"])}</p>')
            if decision:
                chunks.append(f'<p>Decision: {esc(decision["decision"])} by {esc(decision["actor"])} · '
                              f'{esc(decision["reason"])}</p>')
        for annotation in review["event_annotations"]:
            related = " ".join(f'<a href="#{esc(sid)}">{esc(sid)}</a>' for sid in annotation["related_segment_ids"])
            chunks.append(f'<p><a href="#{esc(annotation["event_id"])}">{esc(annotation["event_id"])}</a>: '
                          f'{esc(annotation["description"])} {related}</p>')
        for item in review["uncertainties"]:
            chunks.append(f'<p>Uncertain <a href="#{esc(item["segment_id"])}">{esc(item["segment_id"])}</a>: '
                          f'{esc(item["reason"])}</p>')
    coverage = data.get("review_status", {})
    chunks.append(f'<p>Explicit review coverage: {coverage.get("reviewed", 0)} / '
                  f'{len(data["transcript"]["segments"])} speech segments. '
                  'Coverage is a reviewer attestation, not an accuracy guarantee.</p>')
    chunks += ["</body></html>"]
    return "\n".join(chunks) + "\n"


def render_md(data: dict, reviews: list[dict], work: Path, output: Path) -> str:
    chunks = [f'# {md_text(data["manifest"]["source"]["name"])}',
              "Original-language transcript and timestamped screen observations. "
              "Visual changes and temporal links are candidates for review.",
              f'Source time origin: {data["manifest"]["source_time_origin"]} seconds. '
              'Displayed times are relative to this origin.']
    for _, _, kind, item in document_items(data):
        if kind == "segment":
            chunks += [f'## {timestamp(item["start"])}–{timestamp(item["end"])} · {item["id"]}', md_text(item["text"])]
        elif kind == "frame":
            relative = f'frames/{item["id"]}.jpg'
            target = asset_path(output, relative)
            target.parent.mkdir(parents=True, exist_ok=True)
            source = asset_path(work, item["image"])
            if source.resolve() != target.resolve():
                shutil.copyfile(source, target)
            chunks += [f'![Screen at {timestamp(item["time"])}]({relative})',
                       f'{item["id"]} · {timestamp(item["time"])} · {md_text(", ".join(item["reasons"]))}']
            if item.get("ocr"):
                chunks += ["OCR:", md_text(item["ocr"])]
        else:
            lo, hi = item["observed_between"]
            chunks.append(f'Visual change candidate {item["id"]}: observed between {timestamp(lo)} and {timestamp(hi)}.')
    if data.get("audit"):
        chunks += ["## Speech coverage audit", "Candidates only; coverage does not prove completeness."]
        chunks += [f'- {timestamp(c["start"])}–{timestamp(c["end"])}: possible missing speech'
                   for c in data["audit"]["candidates"]]
    for review in reviews:
        chunks += [f'## Review: {md_text(review["reviewer"])}', "Suggestions; original speech is preserved above."]
        for index, correction in enumerate(review["corrections"]):
            chunks.append(f'{correction["segment_id"]}: {md_text(correction["original_text"])} → '
                          f'{md_text(correction["proposed_text"])}  \nReason: {md_text(correction["reason"])}')
            pid = "p" + fingerprint({"report": fingerprint(review), "index": index})[:20]
            history = [d for d in data.get("decisions", []) if d.get("proposal_id") == pid]
            if history:
                decision = sorted(history, key=lambda d: d["created"])[-1]
                chunks.append(f'Decision: {decision["decision"]} by {md_text(decision["actor"])} · {md_text(decision["reason"])}')
        for annotation in review["event_annotations"]:
            chunks.append(f'{annotation["event_id"]}: {md_text(annotation["description"])} '
                          f'({", ".join(annotation["related_segment_ids"])})')
        for item in review["uncertainties"]:
            chunks.append(f'Uncertain {item["segment_id"]}: {md_text(item["reason"])}')
    chunks.append(f'Explicit review coverage: {data.get("review_status", {}).get("reviewed", 0)} / '
                  f'{len(data["transcript"]["segments"])} speech segments. '
                  'Coverage is a reviewer attestation, not an accuracy guarantee.')
    return "\n\n".join(chunks) + "\n"


def render_subtitles(data: dict, *, vtt: bool = False) -> str:
    chunks = ["WEBVTT\n"] if vtt else []
    for index, segment in enumerate(data["transcript"]["segments"], 1):
        text = segment["text"].strip()
        # WebVTT cue text is markup; escape literal angle brackets and ampersands.
        if vtt:
            text = html.escape(text, quote=False)
        chunks.append(f'{index}\n{timestamp(segment["start"], srt=not vtt)} --> '
                      f'{timestamp(segment["end"], srt=not vtt)}\n{text}\n')
    return "\n".join(chunks)


def export(work: Path, output: Path, formats: list[str]) -> list[Path]:
    unknown = set(formats) - FORMATS
    if unknown:
        raise ValueError(f"Unknown output formats: {', '.join(sorted(unknown))}")
    if not formats:
        raise ValueError("Choose at least one output format")
    data = read_json(work / "timeline.json")
    from .evidence import inspection_frames
    from .triage import decisions, status
    data["inspection"] = inspection_frames(work, data["snapshot"])
    data["decisions"] = decisions(work, data["snapshot"])
    if (work / "coverage").exists() or (work / "decisions").exists():
        data["review_status"] = status(work)
    reviews = load_reviews(work, data["snapshot"])
    output.mkdir(parents=True, exist_ok=True)
    paths = []
    document = render_html(data, reviews, work) if {"html", "pdf"} & set(formats) else None
    for fmt in dict.fromkeys(formats):
        path = output / (f"transcript.{fmt}" if fmt in {"json", "srt", "vtt"} else f"index.{fmt}")
        if fmt == "json":
            write_json(path, {**data, "reviews": reviews})
            # JSON is a portable document plus its relative image assets.
            for frame in data["visual"]["frames"] + data["inspection"]:
                source = asset_path(work, frame["image"])
                target = asset_path(output, frame["image"])
                target.parent.mkdir(parents=True, exist_ok=True)
                if source.resolve() != target.resolve():
                    shutil.copyfile(source, target)
        elif fmt == "html":
            path.write_text(document, encoding="utf-8")
        elif fmt == "md":
            path.write_text(render_md(data, reviews, work, output), encoding="utf-8")
        elif fmt in {"srt", "vtt"}:
            path.write_text(render_subtitles(data, vtt=fmt == "vtt"), encoding="utf-8")
        elif fmt == "pdf":
            from playwright.sync_api import sync_playwright

            with sync_playwright() as playwright:
                browser = playwright.chromium.launch()
                try:
                    page = browser.new_page()
                    page.route("**/*", lambda route: route.abort())
                    page.set_content(document, wait_until="load")
                    page.pdf(path=str(path), format="A4")
                finally:
                    browser.close()
        paths.append(path)
    return paths
