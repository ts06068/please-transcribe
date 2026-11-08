# Polish uncertain speech with your agent

Polishing corrects suspected recognition errors using the audio and screen
context. It preserves the spoken language and meaning, including repetitions,
fillers, numbers, and negations. It is not a summary or a translation.

The original ASR stays in `transcript.raw.json`. Approved edits are exported as a
separate version under `exports/polished/`. Default exports remain original.

## Ask Codex, Claude Code, or another agent

Open the session's repository/workspace in an agent with file and terminal access:

> Polish uncertain speech in `work/session`. Follow AGENTS.md. Prepare focused
> polish packets, inspect the actual audio and related images, and submit a review
> report. Accept only audio-supported corrections; defer anything you cannot
> verify. Export a separate polished version and report remaining uncertainties.
> Preserve the original transcript, repetitions, and spoken languages.

For an MCP-connected agent, ask it to call `prepare_polish`, read the written
instructions and packet files, inspect evidence with the audio/image tools, submit
its report with `submit_review`, record decisions with `decide_candidate`, and call
`export_polished`. The tools use session names relative to the MCP root.

The chosen harness must actually support audio inspection to accept speech edits.
If it only supports text/images, it can propose corrections and uncertainties;
it must defer audio verification. A returned WAV file does not establish that the
agent heard it. No vendor account tokens or paid API integration are required by
the application; your harness's own usage limits still apply.

## CLI and file workflow

Prepare a page of candidates:

```sh
please-transcribe review polish --work work/session --limit 10 --offset 0
```

The request, instructions, response schema, and packets are in `review/polish/`.
Packets include bounded audio clips, nearby original speech, and screen references.
The request reports `next_offset`; continue through all requested pages before
claiming a complete review. Accepted/rejected candidates are excluded from the
pending queue; deferred candidates remain available.

Have the agent write a normal review response, then import it:

```sh
please-transcribe review import response.json --work work/session
please-transcribe review focus --work work/session
```

Importing a proposal does not approve it. Find its `correction_proposal` candidate
ID, then record a decision after inspecting its audio:

```sh
please-transcribe review decide CANDIDATE_ID --work work/session \
  --decision accepted --actor 'My reviewer' --reason 'Verified against audio' \
  --audio-inspected
please-transcribe export --work work/session --output work/session/exports \
  --text-version polished --formats md,html,pdf,json,srt,vtt
```

Use `deferred` or `rejected` when appropriate. The same flags must not be set
merely because OCR or another ASR guess looks plausible. Contradictory accepted
corrections for one segment block export until one is rejected or deferred.

For an existing automation wrapper, `review polish --command-json '[...]'` uses
that wrapper's supported stdin/stdout protocol: the focused request arrives on
stdin, and a normal review response returns on stdout. It imports proposals but
does not auto-accept them. The project does not invent vendor CLI flags or manage
subscription credentials. The Python functions are `prepare_polish` and
`export_polished` in `avtimeline.api`.

## Output contract

All six formats can contain the polished text. Original exports are not replaced.
The polished JSON includes the edited transcript, the complete `original_transcript`
with original word data, applied proposal/decision IDs, changed-segment count, and
unresolved-candidate count. HTML/Markdown/PDF label the polished view and show the
remaining candidate count. Subtitle files contain the edited segment text.

Segment IDs, ordering, and times remain unchanged. Corrected words are not forced
aligned again: edited segments have no word alignment or original ASR score, and
are labeled as segment-only. The original word times/scores remain in the baseline
and in `original_transcript`. Unaccepted text stays unchanged. Successful polishing
does not guarantee that all words were recognized or that all uncertainty is resolved.
