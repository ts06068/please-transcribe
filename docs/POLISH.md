# Automatic polishing and raw-text annotations

`run` and `batch` polish uncertain speech by default. The ASR baseline stays in
`transcript.raw.json`; corrected files go under `exports/polished/`. The result's
`primary_exports` points to that folder when polishing finishes.

Polishing corrects recognition errors while preserving the spoken language,
meaning, repetitions, fillers, numbers, and negations. A correction is applied
only when the agent explicitly records audio inspection and does not flag the
segment as uncertain. Unverified passages retain their original text.

## Through Codex, Claude Code, or another active agent

Ask your agent to transcribe the recording as usual. Follow-up polishing is part
of that request: the agent reads `review/polish/instructions.md`, inspects the
packets, and returns the automatic response. It should not ask you to run review
commands or approve each correction.

With MCP, the agent calls `polish_session`, inspects the returned evidence, and
calls `submit_polish`. The response either contains completed exports or the next
page. Continue until completion. Acceptance decisions and exports happen in code;
there is no manual `decide_candidate` step in this workflow.

A terminal agent can write its response and run:

```sh
please-transcribe review apply-polish automatic-response.json --work work/session
```

The command continues through export or returns the next page for the agent.
Requested formats, including PDF, are retained across this handoff. A completed
pass may still have unresolved candidates; the result reports their count.

## Unattended CLI

Configure an existing harness wrapper once. It receives the request JSON on stdin,
runs with the session as its working directory, and returns automatic-response JSON
on stdout. Diagnostics belong on stderr. For example, in a POSIX shell:

```sh
export PLEASE_TRANSCRIBE_POLISH_COMMAND='["python", "/absolute/path/my_agent_wrapper.py"]'
please-transcribe run recording.mp4 --work work/session --formats md,html,pdf,json
```

Alternatively pass the JSON argv as `--polish-command-json` on run/batch, or use
`review polish --command-json` for an existing session. No shell expansion occurs.
The application does not supply vendor CLI flags, copy account tokens, or sign
into a harness on your behalf. Use your harness's supported login and modalities.

Without a wrapper, pending speech returns `state=awaiting_polish` and a request
for the active agent; CLI exit status 2 means this handoff is unfinished. This is
not reported as a completed LLM correction. `--polish off` requests baseline-only
processing. Failures keep the baseline, request, and successful output formats.

## Automatic response

The generated `automatic-response.schema.json` defines the envelope. A minimal
response has this shape; replace the IDs with those from the current request:

```json
{
  "request_id": "copy from the request",
  "review": {
    "snapshot": "copy from the request",
    "reviewer": "your agent",
    "corrections": [],
    "event_annotations": [],
    "uncertainties": []
  },
  "audio_inspected_segment_ids": [],
  "images_inspected_segment_ids": []
}
```

Corrections use the normal `response.schema.json`: exact `original_text`, a
`proposed_text`, reason, segment ID, and cited frame IDs. Inspection lists must
reference speech in the supplied packets. Empty lists do not imply inspection.
Returning a WAV file does not establish that the model heard it; image/text-only
harnesses must leave audio inspection empty and retain uncertainties.

The application imports the report, accepts audio-verified edits, records coverage,
and exports automatically. Legacy wrappers returning only a normal review report
can still propose corrections, but do not imply audio inspection. Contradictory
accepted edits block export until resolved rather than silently choosing a text.

Each candidate page is processed once per pass, including deferred passages.
Repeating run reuses ASR, reports, and unchanged exports. Use `--polish-refresh` on
run/batch, `review polish --refresh`, or MCP `polish_session(refresh=true)` to
revisit unresolved candidates. Previous reports and decisions stay on disk.

## HTML and PDF

Changed phrases have native HTML `<mark title="Raw text: …">` elements. Hover to
read the original recognized text. Unresolved, unchanged passages also expose
their raw text. No CSS, JavaScript, or external tooltip library is added to HTML.

PDF contains yellow Highlight annotations with the raw text in their comment
contents and popup. Use a reader's annotation tools to read the comments. Location
comes from Chromium's print link rectangles, so repeated phrases, Unicode text,
line wrapping, and page breaks do not rely on guessing a text-search match. The
private print intermediate uses locator links; those links are removed from the
finished PDF. PDF annotation processing uses the optional PyMuPDF dependency.

Polished JSON includes the full `original_transcript`, changed-segment records,
raw annotation offsets, and unresolved count. IDs, segment ordering, and segment
times stay unchanged. Corrected words are not realigned and have no inherited ASR
word score; original word times/scores remain in the baseline. This workflow does
not guarantee perfect transcription or recover speech missing outside existing
segments. Such gaps remain candidates for re-transcription and review.
