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

Initial release regression results: **43 tests passed, zero skipped** on Linux ARM64
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

## Agent polishing, cloud inputs, and YouTube (2026-11-09)

- Linux ARM64 with all optional test dependencies: **55 tests passed, zero skipped**.
  macOS: **55 tests run, four optional media/PDF tests skipped**.
- Actual MCP SDK calls exercised polish preparation, review submission, acceptance
  decisions, and polished JSON export. Regression tests verify all five browser-free
  formats, original-file preservation, conflict rejection, and segment-only timing
  for corrected words. The existing wrapper protocol also received a focused request
  and returned an imported fixture-agent response.
- A real demo video was processed through a read-only external Docker bind mount,
  without an intake video copy. Original and polished exports completed in all six
  formats, including PDF; this integration reused the saved demo ASR as a fixture.
- A live public YouTube video was downloaded with Node.js/EJS, then transcribed by
  the remote MLX large-v3 service and exported in all six formats. URL cache reuse,
  common YouTube URL forms, runtime selection, and Unicode download paths are tested.
- Read-only rclone command construction, VFS disk-cache disabling, existing-file
  protection, and Windows drive-letter handling are tested. Live Google/Microsoft
  account authentication and OS FUSE/WinFsp mounting were not performed. These remain
  operator setup requirements; the application reuses an existing rclone configuration.

No external YouTube recording or transcript is included in the repository. The
published demo and its image-only review provenance remain unchanged.

## Automatic polishing and raw-text annotations (2026-11-09)

- Linux ARM64 with media, PDF, URL, and MCP dependencies: **65 tests passed, zero
  skipped**. macOS: **65 tests run, six optional media/PDF tests skipped**.
- Tests execute the default pipeline handoff, a configured harness, and actual MCP
  SDK calls through `polish_session` and `submit_polish`. Audio-verified fixture
  edits are accepted and exported without manual decision commands. Missing audio
  defers edits; stale responses and out-of-scope inspection claims are rejected.
- A separate synthetic session completed the handoff and exported HTML, PDF, and
  JSON with English, Korean, and Spanish edits and an unresolved passage. Raw ASR
  stayed byte-for-byte unchanged. These are controlled test attestations, not an
  accuracy assessment or a claim of real audio review by a model.
- Chromium and PyMuPDF generated native yellow PDF Highlight annotations with
  Unicode raw-text comments and popups. Tests check annotation color, visible yellow
  pixels, repeated-phrase placement, and a passage spanning multiple PDF pages.
  Temporary locator links are absent from the PDF. Rendered pages were also
  inspected visually.
- HTML checks cover exact raw-text tooltips, escaping and multiline text, with no
  CSS or JavaScript. Resumption reuses review results and unchanged exports while
  preserving the initially requested formats. A refresh starts another review pass.
- The wheel builds successfully, and the existing public demo still passes its
  six-format integrity check. No private recordings or generated fixture outputs
  were added to the public repository.
