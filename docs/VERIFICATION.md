# Release verification

Verified on 2026-11-08. Runtime logs and private connection profiles are excluded
from Git. The public synthetic demo contains no private recording or credentials.

| Requirement | Executed verification |
| --- | --- |
| Installation and actual processing | SSH installation from the Linux development container to an Apple Silicon Mac; a separate fresh Docker client called the native MLX large-v3 worker and generated Markdown, HTML, PDF, JSON, SRT, and VTT. Environment diagnosis passed, including the PDF browser. |
| Install-free demo | [HTML](../examples/demo/index.html), [Markdown](../examples/demo/index.md), [PDF](../examples/demo/index.pdf), source video and provenance checked in. `python scripts/check_demo.py` verifies five original speech segments, thirteen extracted images, five playable review excerpts, all six formats, and absence of CSS/JavaScript and private runtime paths. |
| Diagnosis, progress and resumption | All processing stages reused on a second fresh-container invocation; raw transcript modification time unchanged. Regression tests cover interrupted stages, killed lock holders, changed inputs, partial export failures, and structured progress. |
| Omission and uncertainty review | Actual VAD audit and candidate pages generated. Demo retains ASR mistakes, correction proposals, a deferred decision, and explicit image-only review coverage. Tests verify audio sample coordinates, append-only decisions, and immutable baseline speech. |
| Agent investigation | Actual MCP SDK stdio calls exercised context, image/audio content, dense frame citations, review submission, coverage, decisions, and root-directory confinement. Python and CLI use the same operations. |
| Independent remote serving | Actual Docker controller stop unloaded the native model; start reloaded it. SSH reinstall preserved keys and the existing worker when unchanged. Latest service recovered a lost client receipt using the same job without recomputation. HTTP tests cover job ownership, queue limits, failed/expired job retry, and attempt history. |
| URL and batch input | Actual HTTP video download plus local recording and missing input produced two completed items and one isolated failure, with six formats for each success. Repetition reused downloads and transcripts. Latest tests also exercise URL metadata preflight and cache validation. |

Latest full regression results: **43 tests passed, zero skipped** on Linux ARM64
with media/CPU/PDF/URL/MCP dependencies; **43 tests run, four skipped** on macOS
without local FFmpeg/Chromium. The client Docker image was built with PDF support.
Korean and Spanish synthetic recordings were separately transcribed in their
original languages through the Mac service.

Limitations: the demo uses synthesized speech and controlled screen changes; it
does not measure accuracy on arbitrary presentations. The demo review inspected
images, not audio, and does not claim complete verification. Windows and additional
Python versions have a configured CI matrix but were not executed locally.
Cloudflare routing from an external computer was not verified. The Mac MLX worker runs natively because Docker's Linux
containers do not expose Apple's Metal acceleration; Docker controls its lifecycle.

Other model-serving and development containers remained healthy. No unrelated
model-serving configuration or private-network route was changed.
