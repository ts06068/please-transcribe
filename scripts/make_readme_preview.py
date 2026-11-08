"""Capture the demo's real HTML and show it beside the input recording.

Requires the pdf extra and Playwright Chromium. This styles a README illustration,
not the exported document. The original recording and HTML remain unchanged.
"""
from __future__ import annotations

import argparse
import hashlib
import html
import math
from pathlib import Path


def main():
    from playwright.sync_api import sync_playwright

    root = Path(__file__).resolve().parents[1]
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=root / "docs/images/readme-preview.png")
    parser.add_argument("--scratch", type=Path, default=root / "work/readme-preview")
    args = parser.parse_args()
    output, scratch = args.output.resolve(), args.scratch.resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    scratch.mkdir(parents=True, exist_ok=True)
    demo = root / "examples/demo"
    source, exported = demo / "source.mp4", demo / "index.html"
    original_hashes = [hashlib.sha256(path.read_bytes()).hexdigest() for path in (source, exported)]
    excerpt = scratch / "html-excerpt.png"

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch()
        page = browser.new_page(viewport={"width": 700, "height": 900}, device_scale_factor=2)
        page.goto(exported.as_uri(), wait_until="networkidle")
        page.evaluate("document.getElementById('f000004').scrollIntoView({block: 'start'})")
        frame = page.locator("#f000004").bounding_box()
        speech = page.locator("#s000003").bounding_box()
        assert frame and speech
        assert page.locator("#s000003 p").first.inner_text().strip() == "Running the program prints 42."
        top = max(0, math.floor(frame["y"]))
        height = math.ceil(speech["y"] + speech["height"] + 10 - top)
        page.screenshot(path=str(excerpt), clip={"x": 0, "y": top, "width": 700, "height": height})

        illustration = scratch / "comparison.html"
        illustration.write_text(f'''<!doctype html>
<html lang="en"><head><meta charset="utf-8"><title>please-transcribe example</title>
<style>
* {{ box-sizing: border-box; }}
body {{ margin: 0; background: #fff; color: #17202a; font-family: Arial, sans-serif; }}
main {{ width: 1200px; padding: 36px 40px 24px; }}
h1 {{ margin: 0 0 10px; font-size: 34px; letter-spacing: -0.7px; }}
header p {{ margin: 0 0 30px; font-size: 19px; color: #485461; }}
.columns {{ display: grid; grid-template-columns: 376px 44px 700px; align-items: start; }}
h2 {{ margin: 0 0 8px; font-size: 22px; }}
.label {{ margin: 0 0 16px; color: #596572; font-size: 14px; }}
video {{ display: block; width: 376px; height: 212px; background: #000; border: 1px solid #d4dbe1; }}
.arrow {{ padding-top: 145px; text-align: center; font-size: 28px; color: #326baf; }}
.export {{ display: block; width: 700px; border: 1px solid #d4dbe1; }}
dl {{ margin: 24px 0 0; line-height: 1.5; }}
dt {{ margin-top: 16px; font-size: 13px; font-weight: bold; color: #596572; }}
dd {{ margin: 2px 0 0; font-size: 17px; }}
footer {{ margin-top: 24px; padding-top: 16px; border-top: 1px solid #e0e5e9; font-size: 13px; color: #596572; }}
</style></head><body><main>
<header><h1>From video to transcript</h1>
<p>Read what was said alongside what was on screen.</p></header>
<div class="columns">
<section><h2>Input video</h2><p class="label">Original recording, paused at 00:07</p>
<video controls muted preload="auto" src="{html.escape(source.as_uri(), quote=True)}"></video>
<dl><dt>TRANSCRIPT</dt><dd>Original spoken language</dd>
<dt>TIMING</dt><dd>Word and segment timestamps</dd>
<dt>FILES</dt><dd>HTML, Markdown, PDF,<br>JSON, SRT, and VTT</dd></dl></section>
<div class="arrow" aria-hidden="true">→</div>
<section><h2>Generated HTML</h2><p class="label">An excerpt of the actual export, with default browser formatting</p>
<img class="export" src="{html.escape(excerpt.as_uri(), quote=True)}" alt="HTML export showing a screenshot and timestamped speech"></section>
</div><footer>Real demo recording and exported HTML. Narration is synthesized.</footer>
</main></body></html>''', encoding="utf-8")
        page.set_viewport_size({"width": 1200, "height": 1100})
        page.goto(illustration.as_uri(), wait_until="networkidle")
        page.locator("video").evaluate("""video => new Promise((resolve, reject) => {
            const seek = () => {
                video.addEventListener('seeked', resolve, {once: true});
                video.currentTime = 7;
            };
            video.addEventListener('error', () => reject(new Error('Demo video could not be decoded')), {once: true});
            if (video.readyState >= 1) seek();
            else video.addEventListener('loadedmetadata', seek, {once: true});
        })""")
        # Let the browser present a decoded frame and clear its native loading
        # indicator before capturing the paused player.
        page.locator("video").evaluate("video => video.play()")
        page.wait_for_function("""() => {
            const video = document.querySelector('video');
            return video.readyState === 4 && video.currentTime >= 7.2;
        }""")
        page.locator("video").evaluate("video => video.pause()")
        page.evaluate("() => new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve)))")
        page.locator("video").hover()
        assert page.locator("video").evaluate("v => v.paused && v.currentTime >= 7 && v.currentTime < 8")
        page.locator("main").screenshot(path=str(output))
        browser.close()

    assert original_hashes == [hashlib.sha256(path.read_bytes()).hexdigest() for path in (source, exported)]
    print(output)


if __name__ == "__main__":
    main()
