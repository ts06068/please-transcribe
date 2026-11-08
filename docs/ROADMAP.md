# Release scope

This document records the scope of the first public release. A row
is complete only after its implementation and relevant execution evidence exist.
Runtime records under `work/` are private local evidence, not distributable fixtures.

| Priority | Requirement | Completion evidence |
| --- | --- | --- |
| P0 | Install and finish a real media pipeline | Clean client installation; actual ASR, screen extraction, all six exports; regression checks |
| P0 | View a real output without installing anything | Checked-in standalone HTML, Markdown and companion images, downloadable PDF, source/demo instructions and honest provenance |
| P1 | Diagnose environment, show progress, resume automatically | Dependency/server diagnostics, live stage events, durable stage records, interrupted-run and changed-input tests |
| P1 | Review omissions and uncertain speech efficiently | Candidate queue with audio excerpts and screen evidence; original/proposal/decision history kept separate; review coverage |
| P1 | Let agents investigate only the required time range | Shared Python/CLI/MCP tools for context, denser frames, audio excerpts, review state, and validated proposals |
| P2 | Remote transcription and independent model serving | Authenticated jobs, ownership, retry/resume; SSH provisioning; Docker start/stop of native Mac worker; end-to-end client test |
| P2 | URL input and multiple videos | Resumable URL intake, local/URL batch, per-item failures, stable session identities and batch index |

Cross-cutting invariants: original-language speech retained, no silent rewriting,
no CSS/JavaScript in exported HTML, original screen images, platform-independent
clients, optional engines/harnesses, no committed credentials or private recordings.

All seven requirements above have implementation and execution evidence for this
release. See [the verification record](VERIFICATION.md) for environments, exact
checks, artifacts, and limitations. Completion does not imply perfect ASR or that
every operating system or private-network route has been exercised.
