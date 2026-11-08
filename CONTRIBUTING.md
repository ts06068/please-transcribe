# Contributing

Bug reports, documentation improvements, and focused pull requests are welcome.
For larger changes, open an issue describing the use case and proposed behavior
before starting implementation.

## Development setup

Clone the repository, create a Python 3.10+ virtual environment, and install the
extras needed for your work:

```sh
python -m pip install -e '.[media,cpu,url,mcp]'
python -m unittest discover -s tests -v
python scripts/check_demo.py
```

Media tests require FFmpeg/ffprobe. Core tests can run with only `pip install -e .`;
tests requiring unavailable optional dependencies are skipped. For PDF tests,
install the PDF extra and Playwright Chromium, then set `AVTIMELINE_TEST_PDF=1`.
MLX is optional and requires an Apple Silicon macOS host.

Keep changes focused and describe the resulting behavior, how you checked it,
and any limitations. Update relevant CLI examples when changing public arguments.
Use Conventional Commits for commit subjects, such as `feat: add an exporter`,
`fix(client): resume polling`, or `docs: clarify installation`. Use an imperative
description and an optional scope that names the affected component.
Use small synthetic media fixtures; tests should not require private recordings,
API credentials, model downloads, or a running personal server.

## Project invariants

- Preserve original-language ASR text, word data, IDs, and ordering.
- Keep proposals, decisions, and coverage separate from the original transcript.
- Never claim audio or image inspection that did not happen.
- Preserve media timestamps and expose uncertain visual-change intervals.
- Export native HTML without CSS/JavaScript or generated replacement images.
- Keep clients portable and provider-specific dependencies optional.

## Reporting a problem

Include the OS/architecture, Python and please-transcribe versions, backend/model,
sanitized command, expected behavior, and relevant error output. State which
optional extras are installed. Do not attach credentials, private media, or an
entire work directory. When possible, provide a small synthetic reproducer.
For security-sensitive reports, follow [SECURITY.md](SECURITY.md).

Code contributions are distributed under the repository's [MIT license](LICENSE).
Model weights and dependencies remain subject to their respective licenses.
