# Use please-transcribe through an agent

You can ask an agent with file and terminal access to run please-transcribe for
you. This works through the existing CLI and portable review files. No specific
provider SDK or account login is required by the project.

Open this repository in your agent, make the recording accessible, and explicitly
ask it to read [AGENTS.md](../AGENTS.md). The instructions describe environment
checks, processing, evidence review, and validation. They do not install an agent
plugin. A chat-only interface needs tools for executing commands and reading files.

## Copyable requests

### Process and review a recording

> Read AGENTS.md and process `/workspace/videos/presentation.mp4` with
> please-transcribe. Preserve the spoken languages without summarizing or
> translating. Inspect screen images around changes and review their relationship
> to speech. Keep correction proposals and uncertainties separate from the
> original transcript. Export Markdown and plain HTML with extracted images and
> no styles. Execute the steps and return the generated file paths.

The agent must actually inspect an image before claiming visual review. If it
cannot inspect audio, it must say so and retain that uncertainty. Successful
processing and contextual review do not guarantee that no speech was omitted.

### Select a language or process mixed speech

> Follow AGENTS.md to process `/workspace/videos/seminar.mp4`. The recording mixes
> Korean, English, and Spanish. Use a backend that supports per-window language
> detection and retain the original speech. Export HTML, Markdown, JSON, and SRT.

For a mainly single-language recording, specify that language instead. The
`--multilingual` option requires faster-whisper; the MLX adapter supports a selected
or automatically detected language per recording, without per-window switching.

### Export an existing session to PDF

> Export `work/session` to Markdown, HTML, and PDF. Check that dependencies and
> fonts cover the recording's languages. Reuse the completed transcript. Report
> successful files and explain any failed format.

PDF needs Playwright Chromium and suitable fonts. Installing the PDF extra alone
does not install the browser or all required fonts.

### Review existing screen context

> Use the existing transcript in `work/session`. Read its review instructions and
> inspect the actual screen evidence. Save and import a schema-valid report with
> correction proposals and uncertainties, then regenerate HTML and Markdown.
> Preserve the original ASR transcript and report exactly which segments and
> modalities you reviewed.

`run` generates review packets but does not run an LLM review itself. A reviewed
result requires the agent to inspect evidence and submit its report. Reviewing
part of a recording must not be presented as a review of the whole recording.

### Resume interrupted processing

> Resume `work/session` using the same source and options. Preserve the existing
> transcript, reuse verified stages, and retry unfinished work. Export HTML and
> JSON. Do not request another ASR job merely to regenerate an export.

Managed sessions keep a stage ledger. Repeating the same `run` request validates
and reuses completed work. If the remote model job failed, fix the server issue
before explicitly retrying with `--retry-failed`.

### Investigate a short interval

> Investigate speech and screen activity from 1:02 to 1:08. If stored images are
> insufficient, extract denser frames from that interval. Inspect the audio excerpt
> if your tools support it; otherwise report that you did not listen to it. Record
> the evidence IDs, uncertainty, and actual review coverage.

The CLI, Python API, and MCP tools expose bounded context, image, and audio
operations. See [workflows](WORKFLOWS.md) for configuration and limits.

### Process a collection

> Process these local recordings and video URLs independently. Finish valid items
> even if another input fails. Provide a batch index and error details. Reuse
> completed downloads and transcripts when resuming.

URL input requires the URL extra. One URL represents one video; playlists are not
implicitly expanded. The batch result keeps per-item success and failure states.

### Install dependencies or connect a server

> Read README.md, AGENTS.md, and docs/INSTALLATION.md. Install the client dependencies
> in a project virtual environment, connect to the transcription endpoint I provide,
> and verify it with doctor. Process `/workspace/videos/demo.mp4`. Report blocked
> steps or server errors instead of silently running a large local model.

Server provisioning is a separate administrator operation. Only perform it when
explicitly requested, using the supplied SSH identity and a verified host key.
Never stop unrelated model servers or copy account credentials into containers.

## Development containers and remote workspaces

Repository paths and recordings must exist in the environment executing the CLI.
A host path is not automatically visible inside a container. Clone or mount the
repository into a persistent workspace and provide accessible recording paths.

> The repository is `/workspace/please-transcribe` and the input is
> `/workspace/videos/demo.mp4` inside this container. Follow AGENTS.md, check the
> configured remote server, process the recording, review screen evidence, and
> produce HTML and Markdown.

A Linux client can call a Mac ASR server without installing MLX locally. A
previously configured connection profile makes remote ASR the default. Use
`doctor` to verify authentication and model readiness before processing.

## Check the completion report

The agent should report output paths, backend/model/language, audio and image
coverage, uncertain or potentially missing speech, and any failed or unexecuted
steps. HTML is standalone; Markdown must be shared with its companion image
folders. Correction proposals stay separate, and SRT/VTT contain the baseline ASR
text. See [README](../README.md) for entry points and [AGENTS.md](../AGENTS.md) for
the execution contract.
