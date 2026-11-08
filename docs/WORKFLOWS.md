# Processing and investigating recordings

The executable is `please-transcribe`; `avtimeline` remains a compatible alias.
The Python module is `avtimeline`. Every workflow preserves original-language ASR
text; reviews and decisions are separate. Exported HTML uses native components
and extracted images, with no styles or scripts.

## Diagnose before loading a model

```sh
please-transcribe doctor
please-transcribe doctor --pdf --url
please-transcribe doctor --server-only
```

Diagnostics include Python, FFmpeg/ffprobe, media packages, the selected engine or
remote authentication/readiness, and optionally Chromium and the URL downloader.
Output is JSON with checks and actionable installation hints. A failing required
check exits with status 1. `--server-only` checks connectivity without requiring
local media extraction dependencies. `multilingual` in server capabilities means
per-window language switching, not whether individual Korean/Spanish recordings
are supported. Model support and language accuracy vary.

## Automatic resume and progress

```sh
please-transcribe run recording.mp4 --work work/session --progress text
please-transcribe run recording.mp4 --work work/session --progress json
```

Repeat the same request to resume. `pipeline.json` records stage inputs, output
hashes, elapsed time, and failures. Completed media and ASR are reused after their
artifacts are verified. Interrupted preparation is staged privately and rebuilt
without replacing a published baseline. A live OS lock prevents two pipeline
writers; a crashed process releases it without manual lock-file deletion.

Source content, extraction options, and ASR options identify a session. Use a new
session when changing them. A changed existing baseline is an error, not silently
accepted as a new original. URL query strings and API keys are not printed in
progress or copied into distributable output. Private source references remain
in the work directory for later dense frame extraction.

Progress goes to stderr; the final machine-readable result goes to stdout.
For a failed remote model job, fix the server problem and repeat with
`--retry-failed` to request one new attempt. Failure history stays on the server;
this is not an unlimited automatic retry loop.
JSON events include `started`, `progress`, `working`, `completed`, `reused`, and
failure/interruption events. Working heartbeats report elapsed time; percentages
are not invented when the engine cannot provide them. `--progress quiet` suppresses
progress messages.

Export formats are independent stages. For example, a failed PDF does not prevent
HTML/JSON output; install Chromium and repeat the same request to retry PDF without
transcribing again. `run-result.json` lists successful completion or export errors.
The optional `--audit` needs the CPU extra; if it fails, fix the dependency or rerun
without `--audit` and explicitly report that audit coverage was unavailable.

`run` creates review packets; it does not perform an LLM review by itself.

## URL input and batch

Install the optional URL adapter: `python -m pip install -e '.[url]'`.

```sh
please-transcribe run 'https://example.org/recording.mp4' --work work/from-url
please-transcribe batch lecture.mp4 screencast.mkv \
  'https://example.org/demo.mp4' --output-root work/batch
please-transcribe batch --input-list recordings.txt --output-root work/batch
```

The URL adapter uses yt-dlp, with one video per URL. Local files and supported
video URLs can be mixed in a batch; playlists are not implicitly expanded. URL
availability depends on the source site and installed downloader. Partial downloads
are resumed, completed cached inputs are hash checked, and changed URLs use new
sessions. A text input list contains one input per line; blank lines and lines
beginning with `#` are ignored. Relative file paths use the CLI's current directory.

Batch processing is sequential. One bad recording does not prevent the rest from
finishing. Each source gets a stable session name, and `batch.json` plus `index.md`
record per-item results. A batch with any failed item exits with status 1. Re-run
the same inputs to reuse the successful sessions and retry unfinished work.

## Focus on uncertain speech

```sh
please-transcribe review focus --work work/session --limit 20 --offset 0
please-transcribe review status --work work/session
```

The review queue combines optional VAD coverage gaps, low ASR/word scores, repeated
adjacent segments, imported correction proposals, and reviewer uncertainties.
Scores are heuristics, not calibrated confidence or proof that speech is missing.
No candidates does not imply a perfect transcript. VAD audit presence is explicit.

Each page contains the original text, bounded audio excerpts, and screen evidence.
The HTML is standalone and uses basic audio controls and images. JSON supplies a
`next_offset` for the next page. Long candidate spans are explicitly truncated to
a bounded excerpt; query further ranges to finish inspecting them.

Append decisions with an accountable actor and explanation:

```sh
please-transcribe review decide CANDIDATE_ID --work work/session \
  --decision deferred --actor 'My reviewer' --reason 'Audio needs checking'
```

Accepting a speech correction additionally requires `--audio-inspected`. Set this
only after actual audio inspection. Rejected/deferred/accepted history remains in
`decisions/`. Original JSON and subtitles remain unchanged. Regenerated HTML,
Markdown, and JSON include the decision next to the proposal.

Record the scope actually reviewed:

```sh
please-transcribe review coverage s000001 s000002 --work work/session \
  --reviewer 'My reviewer' --images-inspected
```

Audio and image coverage are independent. Proposals alone do not imply the rest
of the recording was reviewed. Coverage records are attestations, not accuracy
certificates. After reviews/decisions, rerun `run` with the same options or export
again; the pipeline invalidates exports when review evidence changes.

## Investigate only a needed interval

```sh
please-transcribe inspect context --work work/session --start 60 --end 75
please-transcribe inspect audio --work work/session --start 60 --end 70
please-transcribe inspect frames --work work/session --start 62 --end 65 \
  --interval 0.25 --max-frames 20
```

Context queries return speech, nearby images, and visual events. Segment pagination
and omitted frames are explicit; a returned reference does not mean its image was
inspected. Queries are bounded to 300 seconds, audio to 120 seconds, and dense
frame extraction to 120 seconds/120 frames. Narrow a range or increase the frame
interval when it exceeds the evidence budget.

Dense extraction uses original video presentation timestamps and verifies source
identity. If the source moved, supply `--source /new/path/video.mp4`. New `d...`
frame IDs are citable by imported review reports. They are saved separately from
the baseline observations and included in subsequent image-bearing exports.
Audio clips retain exact source timeline coordinates; no new silence is invented
where the recording has no audio samples.

## Export formats

```sh
please-transcribe export --work work/session --output work/session/exports \
  --formats md,html,pdf,json,srt,vtt
```

Markdown and JSON use companion image folders; keep those folders when sharing
the output. HTML embeds its images in one file. Subtitles retain the original
ASR text rather than replacing it with review proposals.

For local PDF export, install the extra and its browser in your environment:

```sh
python -m pip install -e '.[pdf]'
python -m playwright install chromium
```

On Linux, you may also need `python -m playwright install-deps chromium`.
Install fonts that cover your recording's languages. PDF prints the plain HTML
using Chromium's default layout. Other formats do not need a browser.
For a Docker image with PDF support, build with `--build-arg WITH_PDF=1`.

## Python and MCP

The shared API is `avtimeline.api`: `get_context`, `clip_audio`, `dense_frames`,
`candidates`, `candidate_context`, `review_status`, `mark_coverage`, `submit_review`,
and `decide`. These functions operate on an existing session `Path`.

```python
from pathlib import Path
from avtimeline.api import get_context, clip_audio

work = Path('work/session')
context = get_context(work, 60, 75, max_frames=8)
audio = clip_audio(work, 60, 70)
```

For an MCP-capable harness, install `python -m pip install -e '.[mcp]'` and configure
a stdio server using your harness's supported configuration mechanism:

```json
{
  "command": "/absolute/path/to/.venv/bin/please-transcribe",
  "args": ["mcp", "--root", "/absolute/path/to/work"]
}
```

This is the server command specification, not a vendor-specific settings file.
Use the Windows virtual environment executable path on Windows. Session names
are relative to the root; paths escaping that root are rejected. Do not share a
root containing sessions another user should not read.

The tools are `get_context`, `get_screen_image`, `get_audio_excerpt`,
`extract_dense_frames`, `get_review_status`, `get_review_candidates`,
`get_candidate_context`, `submit_review`, `mark_reviewed`, and `decide_candidate`.
Screen and audio tools return actual media content. Clients without a modality
must report that limitation rather than claim to have inspected it. Review reports
follow the session's `review/response.schema.json` and keep original text intact.
