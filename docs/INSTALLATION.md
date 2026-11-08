# Client and Mac server installation

`please-transcribe` and `avtimeline` run the same CLI. Client installation and server
provisioning are separate explicit operations. Install the client on Linux,
Windows, or macOS. The native-worker server installer currently targets macOS;
the default MLX backend requires Apple Silicon. It does not modify existing LLM
servers or Cloudflare settings.

## Existing server: install the client only

Copy/clone the project into the execution environment. In a development container,
use a persistent workspace such as `/workspace/please-transcribe`. Mac host paths
are not automatically accessible in the container.

```sh
git clone https://github.com/ts06068/please-transcribe.git
cd please-transcribe
python3 -m venv .venv
# Linux/macOS; on Windows use .venv\Scripts\Activate.ps1
. .venv/bin/activate
python -m pip install -e '.[media]'
```

Install FFmpeg/ffprobe through your operating system's package manager. On Debian
or Ubuntu: `sudo apt-get install ffmpeg python3-venv`. No ASR model is installed in
the client when you use only the media extra.

Ask your administrator for the endpoint and your own API key. Do not put the key
in a shell command or a Git-tracked file. The following prompts for the key with
echo disabled:

```sh
please-transcribe connect --endpoint http://SERVER:8765
please-transcribe doctor
please-transcribe run recording.mp4 --work work/session --language auto
```

Alternatively, use `--api-key-file /private/path/key` or the
`PLEASE_TRANSCRIBE_API_KEY` environment variable. Client profiles are written with
private permissions to `$XDG_CONFIG_HOME/please-transcribe/client.json`, falling
back to `~/.config/please-transcribe/client.json`. `--config` on connect/install
chooses another profile; pass it as `--connection` to run/transcribe/doctor.

`PLEASE_TRANSCRIBE_ENDPOINT` can override a saved endpoint. Once a profile exists,
the default backend is remote. An unavailable remote server is an error; the CLI
does not silently load a local model. Use an explicit local `--backend` to do that.

## Container client alternative

From the repository root:

```sh
docker build -f Dockerfile.client --build-arg WITH_PDF=1 -t please-transcribe-client .
docker run --rm -it -v "$PWD:/work" please-transcribe-client --help
```

The image includes FFmpeg, URL intake, MCP, VAD dependencies, and optional PDF
support, without ASR model weights. Mount a private connection profile separately
and use `--connection` when processing. The endpoint must be reachable from inside
the container. For example, with an existing private profile and recordings under
the current directory:

```sh
docker run --rm \
  -v "$PWD:/work" \
  -v "$HOME/.config/please-transcribe:/config:ro" \
  please-transcribe-client run /work/recording.mp4 \
  --work /work/output --connection /config/client.json --formats md,html,pdf
```

Use an endpoint reachable from the client container; `localhost` refers to that
container. Attach the appropriate Docker network when using a server service alias.

## First-time server setup over SSH

Prerequisites on the Mac: an authorized macOS SSH account, an active desktop login
for that same user, running Docker Desktop, and `uv` on PATH or in `~/.local/bin`.
The installer creates a dedicated Python 3.12 environment. SSH port 22 is the
default; use `--ssh-port` for another port. Passwords are entered using SSH's own
terminal prompt; they are not saved by the installer. Existing SSH keys/config
and optional `--identity` are supported. Verify new SSH host keys normally.

```sh
please-transcribe server install \
  --ssh admin@mac-server \
  --client-name alice \
  --network EXISTING_DEVELOPMENT_DOCKER_NETWORK \
  --endpoint http://please-transcribe-api:8765
```

This uploads **program source only**, installs the native worker environment,
obtains the model, registers a user LaunchAgent, builds two dedicated Docker
containers, and waits for the model to become ready. It then saves this user's
private client profile locally. The endpoint must be reachable from that client.
It is valid for the installer itself to run inside a development container.
In password mode run from a terminal; unattended installation should use an SSH
key/agent or an already authenticated ControlMaster connection.

For installation directly on the Mac, omit `--ssh`. For an existing custom SSH
known-hosts file use `--ssh-known-hosts`; that enables strict host-key checking.
`--ssh-control-path` can reuse an existing authenticated SSH master. Neither option
disables host-key verification.

### Model source and first download

The default is **Whisper large-v3 with MLX float16**. With no custom `--model`,
the installer obtains OpenAI's official large-v3 checkpoint, verifies its published
SHA-256, and converts it to local MLX weights. Conversion needs extra temporary
RAM/disk; allow roughly 10 GB of free disk for packages, checkpoint, and converted
weights. Converted weights and provenance hashes are retained and reused.

To use the Hugging Face MLX distribution instead:

```sh
please-transcribe server install --ssh admin@mac-server \
  --model-source huggingface --client-name alice
```

`--model` selects a custom local path on the server or a compatible model repository.
Auto source selection uses that path/repository when a custom model is specified.
`--backend faster-whisper` installs a native CPU worker instead. Per-window language
switching (`--multilingual`) is supported by faster-whisper, not this MLX adapter;
MLX supports auto or an explicit language per recording. Model capabilities are
reported by `doctor` and checked before upload.

The default readiness timeout is 900 seconds; adjust `--wait-timeout` for a slow
initial download. Failed downloads are not a successful installation. Re-running
installation preserves existing API keys and jobs. An active transcription blocks
an upgrade that would restart its worker. Updating the same source/config is
intended to reuse the existing service.

## Docker and network layout

```text
client container / remote client
       │ authenticated HTTP
       ▼
please-transcribe-api          Docker; no model weights or SSH keys
       │ shared job queue
       ▼
native macOS ASR worker        MLX Metal / optional CPU
       ▲
please-transcribe-model-control Docker; only a lease directory, no network
```

The installer attaches only the API to the existing network named by `--network`.
It does not merge other users' development networks. The service DNS alias is
`please-transcribe-api`. Each user authorized through the server installer gets
an independent key and can retrieve only jobs submitted under that user identity.

For another user, the administrator can repeat `server install` with a distinct
`--client-name` and a separate `--config /private/path/bob-client.json`. Preserve the
existing server options (model, bind address, network, and state directory) when
doing so. An unchanged source/configuration reuses the worker; changing settings
can require a restart. Deliver only that user's key or private profile through an
appropriate private channel. Additional users install the client and run `connect`;
they do not need the Mac's SSH credentials or access to `api-keys.json`. Credentials
are administrator-issued; there is no self-service signup or key-revocation CLI.

The default host binding is `127.0.0.1:8765`; network-attached containers can still
use the service alias. For remote private-network clients, choose a specific Mac
private interface with `--bind-address PRIVATE_IP` and set a reachable `--endpoint`.
Use a trusted private connection or an HTTPS reverse proxy for credentials/audio.
Cloudflare Tunnel/One routing and policies must separately permit the endpoint;
the installer does not create routes, public DNS records, or Cloudflare tokens.

No Docker socket is mounted into either service or the client. The controller
updates a lease once per second; the host supervisor starts/stops only its own ASR
worker process group. If the controller disappears, the lease expires after eight
seconds. Shutdown then allows up to twenty seconds for the worker to exit before
terminating remaining processes. Stopping the controller actually releases the
native model, while the API remains available for status and finished results.

## Start, stop, and diagnose

On the Mac server (or add `--ssh admin@mac-server` from a client):

```sh
please-transcribe server status
please-transcribe server stop
please-transcribe server start
```

The corresponding containers are `please-transcribe-model-control` and
`please-transcribe-api`. Docker Desktop's Start/Stop actions on the **model-control**
container control model execution. Stopping the API alone does not unload the model.
After start, check `doctor` until `health.model.state` is `ready` or `busy`.
Stop interrupts an in-progress transcription; its job can be recovered on start.

Server files are outside the repo under `~/.local/share/please-transcribe/`:

| File | Purpose |
| --- | --- |
| `venv/` | Dedicated native ASR packages |
| `releases/` | Versioned source bundles |
| `models/`, `downloads/` | Local weights and verified original checkpoint |
| `server.json`, `compose.json` | Generated server settings and Docker configuration |
| `api-keys.json` | Private per-user credentials; do not publish |
| `control/` | Lease and readiness state |
| `queue/jobs/` | Requests and results |
| `worker.log`, `supervisor.error.log` | Administrator diagnostics |

Use `--state-dir` consistently for a custom location. One installation owns the
user LaunchAgent label; the installer refuses to take over another state directory.
The model process exits on stop; logs and downloaded models remain for reuse.

## Job behavior and recovery

The API accepts aligned mono 16 kHz PCM16 WAV, not an entire video. Video extraction,
screen evidence, review, and exports stay with the client. Requests are queued and
processed one at a time so the model is shared rather than loaded per user.
The current upload limit is 1 GiB and the queue limit is 20 unfinished jobs.

Raw uploaded audio is deleted after successful/failed processing; the original
client audio stays intact. Completed/failed results expire after seven days when
the worker runs its cleanup loop. Pending inputs remain until processed. Server
administrators should monitor disk use and logs; this is an initial single-server
deployment, not a distributed storage or scheduler service.

The client retains `remote-job.json`. If polling times out or connectivity fails,
repeat the same managed `run` request, or use standalone `transcribe` on the same
prepared session. Polling resumes without a second upload. Uploads also use an
idempotency key, so losing the local receipt still recovers the original job
while the server retains it. The single API process reclaims unpublished partial
uploads when it restarts. After fixing a model failure, use `--retry-failed` on
run/transcribe to re-upload and try once, retaining the previous failed attempt.
The same flag can recover an expired/deleted server job using the local audio.

API routes:

- `GET /healthz` — API and native worker state
- `GET /v1/models` — model/backend and supported request options
- `POST /v1/jobs?language=auto&multilingual=false` — raw WAV upload
- `GET /v1/jobs/ID` — own job status
- `GET /v1/jobs/ID/result` — own completed transcript

All routes require a Bearer API key. The API provides no remote shell, arbitrary
path reading, or model-administration endpoint. Model lifecycle remains an admin
operation through SSH or Docker on the Mac.
