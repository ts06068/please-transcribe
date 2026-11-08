# please-transcribe

**Transcribe what was said. See what was shown.**

[![CI](https://github.com/ts06068/please-transcribe/actions/workflows/checks.yaml/badge.svg?branch=main)](https://github.com/ts06068/please-transcribe/actions/workflows/checks.yaml)
[![Python 3.10+](https://img.shields.io/badge/python-3.10%2B-blue)](pyproject.toml)
[![License: MIT](https://img.shields.io/badge/license-MIT-green)](LICENSE)

Original-language video transcripts linked to timestamped screen images, powered
by Whisper large-v3. Run locally on **Linux, Windows, or macOS**, use Docker, or
connect to a shared ASR server. Review uncertain speech with your preferred agent.

[Demo](#demo) · [Key features](#key-features) · [Getting started](#getting-started) ·
[Usage](#usage) · [Model specification](#transcription-specification) ·
[Outputs](#outputs) · [Documentation](#documentation-and-development) ·
[Contributing](#contributing) · [License](#license)

## Demo

[Read the transcript](examples/demo/index.md) ·
[Download HTML](examples/demo/index.html) · [PDF](examples/demo/index.pdf) ·
[Explore the review queue](examples/demo/review.html)

![Successful Python execution producing 42, extracted from the demo recording](examples/demo/frames/f000004.jpg)

An actual screen image from the demo recording: the program defines `result` and
prints `42`. The transcript links recognized speech to screen evidence before and
after the code changes. This controlled demo uses synthesized narration and actual
Python execution; see its [source and provenance](examples/demo/README.md).

## Key features

- **Follow more than slides.** Capture screen changes in presentations, code demos,
  browser walkthroughs, and lectures, with periodic images for context.
- **Preserve spoken languages.** Transcribe English, Korean, Spanish, and other
  Whisper-supported languages without automatically translating or summarizing.
- **Keep the evidence.** Retain original ASR text, word times, extracted images,
  audio excerpts, and separate correction proposals and decisions.
- **Review with your own agent.** Use files, CLI, Python, or optional MCP with
  Codex, Claude Code, Antigravity, or another capable harness.
- **Resume and batch.** Reuse verified stages after interruptions and process
  local recordings or video URLs with per-item failure reporting.
- **Share portable results.** Export Markdown, plain HTML, PDF, JSON, SRT, and
  WebVTT. HTML uses native elements and embedded screen images, without CSS or JS.

This is an early release. Screen events are pixel-change candidates, and ASR can
omit or misrecognize speech. Review coverage and uncertainty remain explicit;
automatic processing does not certify a complete or accurate transcript.

## Getting started

Choose local transcription or connect to an existing server. Local inference
requires no paid API; model weights are downloaded on first use. The package is
installed from this repository and is not currently published to PyPI.

### Install locally

Requirements: Git, Python 3.10+, and `ffmpeg`/`ffprobe` on `PATH`. Install FFmpeg
using your OS package manager or [FFmpeg's downloads](https://ffmpeg.org/download.html).

```sh
git clone https://github.com/ts06068/please-transcribe.git
cd please-transcribe
python -m venv .venv

# Linux/macOS:
. .venv/bin/activate
# Windows PowerShell instead:
# .venv\Scripts\Activate.ps1

python -m pip install -e '.[media,cpu]'
please-transcribe doctor --backend faster-whisper
please-transcribe run presentation.mp4 --work work/session --backend faster-whisper
```

The default local configuration is Whisper **large-v3, CPU, INT8**. Use `--model tiny`
for a quick functionality check; evaluate accuracy on your recordings before
choosing a production model. Results appear in `work/session/exports/`.
The executable `avtimeline` is an alias; the Python package is `avtimeline`.

### Install with an agent

Open the repository in Codex, Claude Code, or another agent with file and terminal
access, then ask:

> Read README.md, AGENTS.md, and docs/INSTALLATION.md. Install please-transcribe for
> local CPU use in a project virtual environment on this operating system. Install
> the required media tools, verify the CLI, and run doctor. Report the environment
> path, checks performed, and any remaining steps.

Copy more specific prompts for [local use, an existing endpoint, or administrator
server setup](docs/INSTALLATION.md#install-with-an-agent). No agent-account tokens
need to be copied into this project or its containers.

### Connect to an existing server

Install the media extra instead of the CPU extra: `python -m pip install -e '.[media]'`.
Keep FFmpeg installed, and obtain an endpoint and your own API key from the administrator.

```sh
please-transcribe connect --endpoint https://transcribe.example.org
# Enter your API key at the hidden prompt.
please-transcribe doctor
please-transcribe run presentation.mp4 --work work/session
```

With a saved connection profile, remote ASR becomes the default. Extracted audio
goes to the configured server; screen extraction and exports stay on the client.
An unavailable server is reported without silently loading a local model. See
[installation and shared serving](docs/INSTALLATION.md) for per-user keys and networking.

### Run in Docker

The provided Compose configuration runs local CPU inference and caches weights
in a separate volume. Place the recording inside the host `work/` directory:

```sh
mkdir -p work
# Copy recording.mp4 into work/ before running.
docker compose build
docker compose run --rm avtimeline run /work/recording.mp4 --work /work/session
```

For a client without bundled ASR weights, build `Dockerfile.client` and connect to
an existing endpoint. See [the Docker client guide](docs/INSTALLATION.md#container-client-alternative)
for profile mounts and optional PDF support. GPU acceleration requires a compatible
engine, hardware, and runtime; these CPU images do not configure CUDA or Metal.

## Usage

### Transcribe, resume, or process a batch

```sh
please-transcribe run presentation.mp4 --work work/session --language auto
please-transcribe run korean.mp4 --work work/korean --language ko
please-transcribe run spanish.mp4 --work work/spanish --language es
please-transcribe batch lecture.mp4 demo.mkv --output-root work/batch
```

Repeat the same `run` request to resume unfinished work or reuse verified results.
Use a new session for a different recording or changed extraction/ASR settings.
URL inputs require the URL extra; mixed-language per-window detection requires
faster-whisper's `--multilingual`. See [workflows](docs/WORKFLOWS.md) for examples.

### Investigate and review

```sh
please-transcribe review focus --work work/session
please-transcribe inspect context --work work/session --start 60 --end 75
please-transcribe inspect audio --work work/session --start 60 --end 70
please-transcribe inspect frames --work work/session --start 62 --end 65 --interval 0.25
please-transcribe review status --work work/session
```

`run` creates review packets but does not perform an LLM review. To review a session,
open it in your agent and ask:

> Follow AGENTS.md and the session's review instructions. Inspect the evidence,
> record correction proposals and uncertainties separately from the original
> transcript, and import a schema-valid report. Report which audio and images
> you actually inspected, then regenerate Markdown and plain HTML.

Agents can query short intervals, extract denser frames, and record review coverage.
A harness without audio or image capabilities must report that limitation. See
[the agent guide](docs/AGENT_GUIDE.md) and [Python/MCP setup](docs/WORKFLOWS.md#python-and-mcp).

## Transcription specification

The default model is **OpenAI Whisper large-v3**, approximately **1.55 billion
parameters**. The model and inference engine are separate choices. Model details:
[OpenAI Whisper](https://github.com/openai/whisper#available-models-and-languages).

| Setting | Current specification |
| --- | --- |
| Local default | faster-whisper/CTranslate2; `large-v3` resolves to `Systran/faster-whisper-large-v3`; CPU, INT8. |
| Optional local MLX | `mlx-community/whisper-large-v3-mlx`; Apple Silicon; upstream FP16 decoding default. |
| Managed remote service | Server-selected model; default installer converts verified official large-v3 weights to MLX FP16. Optional native CPU worker uses faster-whisper INT8. |
| Audio | Full selected audio stream, mono 16 kHz PCM16 WAV, normalized float32 input to ASR. |
| Task/language | `transcribe`, original language; automatic detection or an explicit language code. Per-window mixed-language detection is a faster-whisper option. |
| Timestamps | Engine segment and word estimates on the common audio/video timeline; no separate WhisperX forced-alignment model. |
| VAD | No separate baseline VAD prefilter; engine silence heuristics still apply. Optional Silero VAD audit flags possible uncovered speech. |
| Visual context | Screen images/OCR support review; they are not fed into Whisper or used to silently rewrite the baseline. |
| Published demo | Remote MLX large-v3, official checkpoint converted to FP16, English; [exact configuration](examples/demo/README.md#transcription-configuration). |

`--model` accepts a compatible repository or local path. `--device` and
`--compute-type` control local faster-whisper; remote precision/hardware are
server-controlled. Unspecified decoder options inherit upstream defaults.
See [the full specification](docs/TRANSCRIPTION.md) for checkpoint hashes, inspected
library versions, decoding settings, long-form behavior, and recorded metadata.

### Platform and deployment support

| Mode | Supported environment |
| --- | --- |
| Local CPU and evidence/export pipeline | Linux, Windows, macOS. |
| Docker CPU | Hosts with a compatible Docker runtime. |
| Remote client | Linux, Windows, macOS, and development containers. |
| Optional MLX engine | Apple Silicon macOS. |
| Bundled automated server provisioner | Currently macOS; this adapter uses LaunchAgents. |

The project is not tied to the inference host. The bundled server provisioner's
current platform restriction does not apply to clients or local/Docker CPU use.
Use the [administrator guide](docs/INSTALLATION.md) for its supported setup path.

## Outputs

```sh
please-transcribe export --work work/session --output work/session/exports \
  --formats md,html,pdf,json,srt,vtt
```

| Format | Files | Sharing notes |
| --- | --- | --- |
| Markdown | `index.md` + images | Keep companion image folders together. |
| HTML | `index.html` | Standalone; native tags and embedded images, no CSS/JavaScript. |
| PDF | `index.pdf` | Chromium's default print layout of the plain HTML. |
| JSON | `transcript.json` + images | Original speech, word times, observations, links, and reviews. |
| SRT / WebVTT | `transcript.srt` / `transcript.vtt` | Original ASR text; review proposals do not replace subtitles. |

PDF needs `python -m pip install -e '.[pdf]'`, `python -m playwright install chromium`,
and fonts covering the recording's languages. On Linux, Chromium may also require
`python -m playwright install-deps chromium`. The other formats do not need a browser.

## Documentation and development

| Guide | Contents |
| --- | --- |
| [Installation](docs/INSTALLATION.md) | Local setup, agent installation prompts, remote clients, Docker, server administration. |
| [Transcription specification](docs/TRANSCRIPTION.md) | Model sources, decoding, timestamps, VAD, and reproducibility boundaries. |
| [Workflows](docs/WORKFLOWS.md) | Progress, resume, URLs/batches, review decisions, Python API, and MCP. |
| [Agent guide](docs/AGENT_GUIDE.md) / [AGENTS.md](AGENTS.md) | Natural-language requests and the agent operating contract. |
| [Release scope](docs/ROADMAP.md) / [verification](docs/VERIFICATION.md) | Implemented scope, executed checks, and limitations. |
| [Security](SECURITY.md) | Data flow, deployment boundaries, and vulnerability reporting. |

```sh
python -m unittest discover -s tests -v
python scripts/check_demo.py
```

Core tests require no model downloads. Optional media/MCP/URL tests need their
extras; PDF tests additionally need Chromium and `AVTIMELINE_TEST_PDF=1`. CI runs a
Linux/macOS/Windows core matrix, media integration, and Docker builds. This is not
a WER/CER or performance benchmark. Sampling can miss visual events, and existing
A/V delay or clock drift is not repaired.

## Contributing

Bug reports, documentation improvements, and focused pull requests are welcome.
Read [CONTRIBUTING.md](CONTRIBUTING.md) for development setup and Conventional
Commit messages. Include a sanitized reproducer when reporting a problem;
keep private recordings, credentials, and work directories out of Git.

## Credits

The pipeline uses [OpenAI Whisper](https://github.com/openai/whisper),
[faster-whisper](https://github.com/SYSTRAN/faster-whisper), optional
[MLX Whisper](https://github.com/ml-explore/mlx-examples/tree/main/whisper),
[FFmpeg](https://ffmpeg.org/), and the optional packages listed in
[pyproject.toml](pyproject.toml).

README organization is inspired by
[Markdownify](https://github.com/amitmerchant1990/electron-markdownify), selected
from [awesome-readme](https://github.com/matiassingers/awesome-readme).

## License

[MIT](LICENSE). Dependencies and model weights retain their own licenses.
The included demo is a controlled synthetic recording with documented provenance.
