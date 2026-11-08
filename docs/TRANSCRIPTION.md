# Transcription specification

This describes the implemented 0.1.0 pipeline, checked on 2026-11-08. It separates
project-controlled settings from inherited engine defaults and the configuration
used for the published demo. Choosing another model or engine changes the result.

## Model and engine

The default model is **OpenAI Whisper large-v3**, an encoder-decoder speech model
with approximately **1.55 billion parameters** and **128 Mel input bins**. Whisper
uses a roughly 30-second audio context; the engine handles long-form decoding.
These are model characteristics, not a promise of equivalent accuracy across
languages or runtimes. Sources: [OpenAI Whisper](https://github.com/openai/whisper),
[large-v3 model card](https://huggingface.co/openai/whisper-large-v3).

| Execution path | Model selection | Hardware and computation | Weight source |
| --- | --- | --- | --- |
| Local faster-whisper | Default `large-v3`; override with `--model` | Default `--device cpu --compute-type int8`; CUDA/auto and other supported compute types are explicit choices | The default alias resolves to `Systran/faster-whisper-large-v3`, a CTranslate2 conversion. |
| Local MLX | Default `mlx-community/whisper-large-v3-mlx`; override with a compatible MLX repository/path | Apple Silicon macOS, upstream FP16 decoding default; custom weights can have their own representation | Hugging Face repository or local directory. |
| Managed remote MLX worker | Installer defaults to large-v3; administrator chooses the model | Native MLX worker, explicitly preloaded with `mlx.core.float16`; Docker API/controller manages access and lifecycle | Default installer downloads the official OpenAI checkpoint and converts it locally. |
| Managed remote CPU worker | Installer's `--backend faster-whisper`; default large-v3 | Native faster-whisper worker, explicitly CPU/INT8 | Compatible CTranslate2 model repository/path. |
| Imported transcript | `--import-transcript` with timestamped JSON | No ASR inference in this project | Supplied engine output; omitted model identity is labeled `external`. |

Local CPU inference and clients support Linux, Windows, and macOS. MLX is optional.
The bundled automated worker provisioning adapter currently uses macOS LaunchAgents;
that limitation does not apply to the client or local/Docker CPU pipeline.

With no explicit `--backend`, a configured endpoint/profile selects remote ASR;
otherwise faster-whisper is used. Remote `--model`, when supplied, must match the
server's advertised model. It does not switch the running server model. Client
`--device`/`--compute-type` do not change the remote worker's hardware or precision.

### Official checkpoint verification

The default managed MLX installation uses the OpenAI large-v3 checkpoint with
this SHA-256:

```text
e5b1a55b89c1367dacf97e3e19bfd829a01529dbfdeefa8caeb59b3f1b81dadb
```

The installer verifies the download before converting weights to FP16 safetensors.
It records the source URL, source hash, dtype, and converted-weight hash in
`models/whisper-large-v3-mlx/provenance.json` under the server state directory.
Reuse checks the converted file against that hash. See
[the conversion implementation](../src/avtimeline/model_download.py).

This path differs from local MLX's default Hugging Face download. The managed
service retains the compatible model label `mlx-community/whisper-large-v3-mlx`
even when it loads the locally converted checkpoint. `--model-source huggingface`
or a custom model changes the provenance; a model label alone is not a weight hash.

## Audio preparation and decoder settings

The first selected non-cover-art video stream and first audio stream are used.
FFmpeg produces mono, 16,000 Hz, signed 16-bit little-endian PCM WAV. Audio samples
are converted to float32 by dividing PCM16 values by 32768 before inference.
Stream offsets are retained relative to the earliest selected stream start;
video evidence uses presentation timestamps from that same origin.

The entire prepared audio is passed to the engine. It is not cut at screen changes.
Long-form windowing and context carry-over belong to the selected engine. The
application does not add an independent overlap-and-stitch transcription pass.
This is offline processing, not a streaming ASR endpoint. The source recording
must contain both video and audio; audio-only input is not currently accepted by
`prepare`/`run`.

| Project-controlled option | Value/behavior |
| --- | --- |
| Task | Always `transcribe`; no automatic translation or summarization. |
| Language | CLI default `auto`, passed as engine language `None`; explicit codes such as `en`, `ko`, `es` are forwarded. |
| Mixed-language detection | `--multilingual` enables faster-whisper per-window detection. MLX rejects this option. Unknown segment language remains `null`. |
| Word timestamps | `word_timestamps=True` for built-in engines. |
| VAD before baseline ASR | faster-whisper explicitly uses `vad_filter=False`; MLX has no separate VAD prefilter in this adapter. |
| Prompts and visual terms | No initial prompt, OCR vocabulary injection, hotword list, or image input is supplied to ASR. |
| Text preservation | Original recognized segment text is stored unchanged. Reviews add proposals and decisions separately. |

Disabling the separate VAD prefilter does **not** disable the decoder's own silence
or quality heuristics. Quiet or unclear speech may still be skipped or misrecognized.

### Inherited defaults inspected

The project does not expose CLI controls for the following decoder options. This
table reflects the installed **faster-whisper 1.2.1** and **mlx-whisper 0.4.3** APIs
inspected for this specification. They are upstream defaults, not version-independent
settings pinned by this application.

| Decoder option | faster-whisper 1.2.1 | mlx-whisper 0.4.3 |
| --- | --- | --- |
| Beam search | `beam_size=5` | No beam size configured; default greedy decoding at temperature 0. |
| Best-of sampling | `best_of=5` | No best-of count configured. |
| Temperature fallback | 0.0, 0.2, 0.4, 0.6, 0.8, 1.0 | Same sequence. |
| Condition on previous text | `True` | `True` |
| No-speech threshold | 0.6 | 0.6 |
| Log-probability threshold | -1.0 | -1.0 |
| Compression-ratio threshold | 2.4 | 2.4 |

The declared package ranges are `faster-whisper>=1.1,<2` and local
`mlx-whisper>=0.4,<1`; the managed MLX installer uses `>=0.4.3,<0.5`. Dependencies
are not locked to the inspected versions. A future compatible release can change
inherited defaults. Source references: [project adapter](../src/avtimeline/transcript.py),
[managed worker](../src/avtimeline/model_service.py),
[faster-whisper API](https://github.com/SYSTRAN/faster-whisper/blob/v1.2.1/faster_whisper/transcribe.py),
[MLX Whisper implementation](https://github.com/ml-explore/mlx-examples/tree/main/whisper).

## Timestamps, omission audit, and review

Word times are the engine's Whisper word-timestamp estimates. There is no separate
WhisperX/wav2vec forced-alignment model. Imported transcripts can omit words;
their supplied timestamps must already use the session's normalized timeline.
Neither word alignment nor speech/image association proves the recognized text
is correct. Segment intervals can overlap; input ordering is retained.

The optional `--audit`/`audit` stage runs Silero VAD from faster-whisper independently
of ASR. It uses upstream `VadOptions()` defaults. In the inspected version these
include threshold 0.5, minimum silence 2000 ms, and speech padding 400 ms. The
application expands existing word coverage by 0.15 seconds on each side, falling
back to segment intervals when words are unavailable, and flags uncovered VAD
speech gaps of at least 0.3 seconds. These candidates are review aids, not proof
that all other speech was transcribed.

Screen images, optional Tesseract OCR, and audio excerpts support later review.
No LLM, VLM, or speaker-diarization model is bundled into baseline ASR. Harnesses
can submit schema-validated proposals, but cannot silently replace the raw transcript.
Word probabilities and `avg_logprob` are engine scores when available; they are
not calibrated confidence percentages. There is no published WER/CER, throughput,
or memory benchmark for this project; evaluate your own media and hardware.

## Recorded metadata and reproducibility

`transcript.raw.json` records `backend`, `model`, `task`, `language`,
`language_requested`, `multilingual`, original segments and optional word data.
Remote transcripts additionally record `server_backend`. The JSON export includes
these fields under `transcript`; it also includes the source hash/time origin under
`manifest`. SRT/VTT contain speech/times, not a full execution manifest.

The current transcript schema does **not** automatically record all installed
package versions, resolved model revisions/hashes, precision, hardware, or effective
decoder defaults. `pipeline.json` keeps stage fingerprints for reuse, not a complete
reproducibility manifest. Server weight provenance is a separate administrator file
and is not automatically copied into client exports. Keep these distinctions when
comparing two runs or citing a model used for an output.

For a reproducible run, record the Git commit, CLI arguments, input SHA-256, exact
model/revision or local weight hash, backend/runtime versions, device and precision,
and effective decoder settings. Save package inventories from both the client and
worker environments; they can differ. For example, in the relevant environment:

```sh
please-transcribe --version
python -m pip list --format=json
please-transcribe run recording.mp4 --work work/reproducible \
  --backend faster-whisper --model large-v3 --device cpu --compute-type int8 \
  --language en --formats md,html,json,srt,vtt
```

For offline use, install dependencies and download weights first, then use a local
model path. Reuse does not retranscribe an existing baseline. A different model,
language, or ASR option requires a new session directory.

The [published demo configuration](../examples/demo/README.md#transcription-configuration)
documents the actual engine/model/precision used for the checked-in example. Its
controlled synthetic recording is a functional demonstration, not an accuracy test.
