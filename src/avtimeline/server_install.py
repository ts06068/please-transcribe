"""Explicit server provisioning over SSH; no credentials in source or argv."""
from __future__ import annotations

import argparse
import hashlib
import io
import json
import os
from pathlib import Path
import platform
import plistlib
import secrets
import shlex
import shutil
import subprocess
import sys
import tarfile
import time

from .connection import save_connection
from .storage import read_json, write_json

DEFAULT_STATE = "~/.local/share/please-transcribe"


def docker() -> str:
    candidate = shutil.which("docker") or str(Path.home() / ".docker/bin/docker")
    if not Path(candidate).is_file():
        raise ValueError("Install and start Docker Desktop on the server first")
    # SSH login shells often omit Docker Desktop's credential helper directory.
    os.environ["PATH"] = str(Path(candidate).parent) + os.pathsep + os.environ.get("PATH", "")
    return candidate


def run(argv, *, check=True):
    return subprocess.run([str(a) for a in argv], check=check, stdout=sys.stderr)


def bundle_source() -> tuple[bytes, str]:
    package = Path(__file__).parent
    root = package.parents[1]
    project = root / "pyproject.toml"
    if project.exists():
        metadata = project.read_bytes()
    else:
        metadata = b'''[build-system]
requires = ["setuptools>=68"]
build-backend = "setuptools.build_meta"
[project]
name = "avtimeline"
version = "0.1.0"
requires-python = ">=3.10"
[project.scripts]
please-transcribe = "avtimeline.cli:main"
avtimeline = "avtimeline.cli:main"
[tool.setuptools.packages.find]
where = ["src"]
'''
    output = io.BytesIO()
    with tarfile.open(fileobj=output, mode="w:gz") as archive:
        for name, body in [("pyproject.toml", metadata), ("README.md", b"# Please Transcribe server\n")]:
            info = tarfile.TarInfo(name)
            info.size = len(body)
            archive.addfile(info, io.BytesIO(body))
        license_path = root / "LICENSE"
        if not license_path.exists():
            import importlib.metadata
            try:
                dist = importlib.metadata.distribution("avtimeline")
                candidate = next((p for p in dist.files or [] if p.name == "LICENSE"), None)
                if candidate:
                    license_path = Path(dist.locate_file(candidate))
            except importlib.metadata.PackageNotFoundError:
                pass
        if license_path.is_file():
            body = license_path.read_bytes()
            info = tarfile.TarInfo("LICENSE")
            info.size = len(body)
            archive.addfile(info, io.BytesIO(body))
        for path in sorted(package.rglob("*.py")):
            if "__pycache__" in path.parts:
                continue
            info = tarfile.TarInfo("src/avtimeline/" + path.relative_to(package).as_posix())
            body = path.read_bytes()
            info.size = len(body)
            archive.addfile(info, io.BytesIO(body))
    payload = output.getvalue()
    # gzip header includes a wall-clock timestamp; hash decompressed content.
    import gzip
    return payload, hashlib.sha256(gzip.decompress(payload)).hexdigest()[:16]


BOOTSTRAP = r'''
import io,json,os,pathlib,shutil,subprocess,sys,tarfile
options=json.loads(sys.argv[1])
state=pathlib.Path(options['state']).expanduser().resolve()
if sys.platform != 'darwin':
    raise SystemExit('This native worker installer currently targets macOS servers')
state.mkdir(parents=True,exist_ok=True,mode=0o700)
release=state/'releases'/options['release_id']
payload=sys.stdin.buffer.read()
release.mkdir(parents=True,exist_ok=True)
with tarfile.open(fileobj=io.BytesIO(payload),mode='r:gz') as archive:
    for entry in archive.getmembers():
        path=(release/entry.name).resolve()
        if not str(path).startswith(str(release.resolve())+os.sep) or not entry.isfile():
            raise SystemExit('Invalid installation archive')
        path.parent.mkdir(parents=True,exist_ok=True)
        path.write_bytes(archive.extractfile(entry).read())
python=state/'venv/bin/python'
uv=shutil.which('uv') or str(pathlib.Path.home()/'.local/bin/uv')
if not pathlib.Path(uv).is_file():
    raise SystemExit('Install uv on the Mac server first (or add it to PATH)')
if not python.exists():
    subprocess.run([uv,'venv','--python','3.12',str(state/'venv')],check=True,stdout=sys.stderr)
extra='mlx-whisper>=0.4.3,<0.5' if options['backend']=='mlx' else 'faster-whisper>=1.1,<2'
subprocess.run([uv,'pip','install','--python',str(python),str(release),extra],check=True,stdout=sys.stderr)
options['release']=str(release)
subprocess.run([str(python),'-m','avtimeline.server_install',json.dumps(options)],check=True)
'''


def provision(options: dict) -> dict:
    if platform.system() != "Darwin":
        raise ValueError("Native server provisioning currently targets macOS; clients remain portable")
    if options["backend"] == "mlx" and platform.machine() != "arm64":
        raise ValueError("MLX requires Apple Silicon")
    state = Path(options["state"]).expanduser().resolve()
    state.mkdir(parents=True, exist_ok=True, mode=0o700)
    for name in ("control", "queue/jobs"):
        (state / name).mkdir(parents=True, exist_ok=True)
    for path in ("source", "config", "keyfile"):
        if path in options:
            raise ValueError("Unknown server installation option")
    binary = docker()
    run([binary, "info", "--format", "{{.ServerVersion}}"])
    network = options.get("network")
    if network:
        run([binary, "network", "inspect", network, "--format", "{{.Name}}"])
    domain = f"gui/{os.getuid()}"
    checked = subprocess.run(["launchctl", "print", domain], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    if checked.returncode:
        raise ValueError("Log into the Mac desktop as this user and start Docker Desktop before provisioning")
    config = {"backend": options["backend"], "model": options.get("model") or (
        "mlx-community/whisper-large-v3-mlx" if options["backend"] == "mlx" else "large-v3"),
        "port": options["port"], "max_upload_bytes": 1024 * 1024 * 1024,
        "max_pending": 20, "retention_days": 7}
    source = options.get("model_source", "auto")
    if source == "auto":
        source = "openai" if options["backend"] == "mlx" and not options.get("model") else "huggingface"
    if source == "openai":
        if options["backend"] != "mlx" or options.get("model"):
            raise ValueError("The OpenAI source currently supports the default MLX large-v3 model only")
        from .model_download import prepare_large_v3
        config["model_path"] = str(prepare_large_v3(state))
        config["model_source"] = "openai-verified-local-conversion"
    cfg_path = state / "server.json"
    previous = read_json(cfg_path) if cfg_path.exists() else {}
    marker = state / "installation.json"
    same = marker.exists() and read_json(marker).get("release_id") == options["release_id"] and previous == config
    if not same:
        for status in (state / "queue/jobs").glob("*/status.json"):
            if read_json(status).get("state") == "running":
                raise ValueError("A transcription is running; retry server installation after it finishes")
    write_json(cfg_path, config)
    key_path = state / "api-keys.json"
    keys = read_json(key_path) if key_path.exists() else {}
    name = options["client_name"]
    if not name or len(name) > 100:
        raise ValueError("Choose a nonempty client name of at most 100 characters")
    if name not in keys:
        keys[name] = secrets.token_urlsafe(32)
        fd = os.open(key_path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w") as handle:
            os.chmod(key_path, 0o600)
            json.dump(keys, handle)
    release = Path(options["release"])
    api_dockerfile = release / "Dockerfile.server"
    api_dockerfile.write_text('''FROM python:3.12-slim-bookworm
ENV PYTHONUNBUFFERED=1
WORKDIR /app
COPY src/avtimeline /app/avtimeline
ENTRYPOINT ["python", "-m", "avtimeline.service_api", "--state", "/state"]
''')
    controller_dockerfile = release / "Dockerfile.control"
    controller_dockerfile.write_text('''FROM python:3.12-slim-bookworm
ENV PYTHONUNBUFFERED=1
WORKDIR /app
COPY src/avtimeline /app/avtimeline
ENTRYPOINT ["python", "-m", "avtimeline.model_service", "controller", "--state", "/state"]
''')
    logging = {"driver": "json-file", "options": {"max-size": "5m", "max-file": "2"}}
    api = {"build": {"context": str(release), "dockerfile": "Dockerfile.server"},
           "container_name": "please-transcribe-api", "restart": "unless-stopped",
           "ports": [f"{options['bind']}:{config['port']}:{config['port']}"],
           "volumes": [f"{cfg_path}:/state/server.json:ro", f"{key_path}:/state/api-keys.json:ro",
                       f"{state / 'queue'}:/state/queue", f"{state / 'control'}:/state/control:ro"],
           "read_only": True, "cap_drop": ["ALL"], "security_opt": ["no-new-privileges:true"],
           "logging": logging, "mem_limit": "256m"}
    if network:
        api["networks"] = {"clients": {"aliases": ["please-transcribe-api"]}}
    compose = {"name": "please-transcribe", "services": {
        "api": api,
        "model-control": {"build": {"context": str(release), "dockerfile": "Dockerfile.control"},
            "container_name": "please-transcribe-model-control", "restart": "unless-stopped",
            "network_mode": "none", "read_only": True, "cap_drop": ["ALL"],
            "security_opt": ["no-new-privileges:true"], "logging": logging, "mem_limit": "64m",
            "stop_grace_period": "40s", "volumes": [f"{state / 'control'}:/state/control"],
            "healthcheck": {"test": ["CMD", "python", "-m", "avtimeline.model_service", "health", "--state", "/state"],
                            "interval": "5s", "timeout": "3s", "start_period": "180s", "retries": 3}},
    }}
    if network:
        compose["networks"] = {"clients": {"external": True, "name": network}}
    compose_path = state / "compose.json"
    write_json(compose_path, compose)
    label = "org.please-transcribe.supervisor"
    plist_path = Path.home() / "Library/LaunchAgents" / (label + ".plist")
    plist_path.parent.mkdir(parents=True, exist_ok=True)
    plist = {"Label": label, "ProgramArguments": [str(state / "venv/bin/python"), "-m",
             "avtimeline.model_service", "supervise", "--state", str(state)],
             "RunAtLoad": True, "KeepAlive": True, "ExitTimeOut": 40,
             "StandardOutPath": str(state / "supervisor.log"), "StandardErrorPath": str(state / "supervisor.error.log")}
    if plist_path.exists():
        old = plistlib.loads(plist_path.read_bytes())
        if old.get("ProgramArguments") != plist["ProgramArguments"]:
            raise ValueError("Another installation owns the supervisor label; use its state directory")
    if not same:
        subprocess.run(["launchctl", "bootout", domain + "/" + label], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        # bootout can return before the old service leaves the GUI domain.
        deadline = time.monotonic() + 30
        while subprocess.run(["launchctl", "print", domain + "/" + label],
                             stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL).returncode == 0:
            if time.monotonic() >= deadline:
                raise ValueError("Previous supervisor is still stopping; retry installation shortly")
            time.sleep(0.25)
    plist_path.write_bytes(plistlib.dumps(plist))
    loaded = subprocess.run(["launchctl", "print", domain + "/" + label], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    if loaded.returncode:
        run(["launchctl", "bootstrap", domain, plist_path])
    run([binary, "compose", "-f", compose_path, "up", "-d", "--build"])
    from .connection import request
    local_host = "127.0.0.1" if options["bind"] == "0.0.0.0" else options["bind"]
    probe = {"endpoint": f"http://{local_host}:{config['port']}", "api_key": keys[name]}
    print("Waiting for the ASR model to load (first use downloads weights)...", file=sys.stderr, flush=True)
    deadline = time.monotonic() + options.get("wait_timeout", 900)
    while time.monotonic() < deadline:
        try:
            health = request(probe, "/healthz", timeout=3)
            if health["model"]["state"] in ("ready", "busy"):
                break
        except (OSError, ValueError):
            pass
        time.sleep(2)
    else:
        raise ValueError(f"Model did not become ready; inspect {state / 'worker.log'} and retry installation")
    write_json(marker, {"release_id": options["release_id"], "backend": config["backend"], "model": config["model"]})
    return {"endpoint": options["endpoint"], "api_key": keys[name], "user": name,
            "server": {"model": config["model"], "backend": config["backend"]}}


def ssh_argv(args) -> list[str]:
    if not shutil.which("ssh"):
        raise ValueError("Install an OpenSSH client before using remote server installation")
    command = ["ssh", "-o", "ConnectTimeout=10", "-p", str(args.ssh_port)]
    if args.identity:
        command += ["-i", str(args.identity)]
    if args.ssh_known_hosts:
        command += ["-o", "UserKnownHostsFile=" + str(args.ssh_known_hosts), "-o", "StrictHostKeyChecking=yes"]
    if args.ssh_control_path:
        command += ["-o", "ControlPath=" + args.ssh_control_path]
    return command + [args.ssh]


def install(args) -> Path:
    if not 1024 <= args.port <= 65535:
        raise ValueError("Use an unprivileged server port between 1024 and 65535")
    payload, release_id = bundle_source()
    host = args.ssh.rsplit("@", 1)[-1] if args.ssh else "127.0.0.1"
    endpoint = args.endpoint or f"http://{host}:{args.port}"
    options = {"state": args.state_dir, "release_id": release_id, "backend": args.backend,
               "model": args.model, "port": args.port, "bind": args.bind_address,
               "network": args.network, "client_name": args.client_name, "endpoint": endpoint}
    options["wait_timeout"] = args.wait_timeout
    options["model_source"] = args.model_source
    if args.ssh:
        command = ssh_argv(args) + [shlex.join(["python3", "-c", BOOTSTRAP, json.dumps(options)])]
    else:
        command = [sys.executable, "-c", BOOTSTRAP, json.dumps(options)]
    result = subprocess.run(command, input=payload, stdout=subprocess.PIPE, check=True)
    # The SSH channel carries the generated API key directly into a private local
    # profile. Never print it or include it in shell arguments or the repository.
    data = json.loads(result.stdout)
    return save_connection(data, args.config, check=False)


def lifecycle(args) -> None:
    if args.ssh:
        code = "from pathlib import Path; import subprocess,sys; p=Path(sys.argv[1]).expanduser(); subprocess.run([str(p/'venv/bin/python'),'-m','avtimeline.cli','server',sys.argv[2],'--state-dir',str(p)],check=True)"
        subprocess.run(ssh_argv(args) + [shlex.join(["python3", "-c", code, args.state_dir, args.action])], check=True)
        return
    state = Path(args.state_dir).expanduser().resolve()
    base = [docker(), "compose", "-f", str(state / "compose.json")]
    if args.action == "status":
        subprocess.run(base + ["ps"], check=True)
        from .model_service import safe_read
        print(json.dumps(safe_read(state / "control/status.json"), indent=2))
    elif args.action == "start":
        subprocess.run(base + ["up", "-d"], check=True)
    elif args.action == "stop":
        subprocess.run(base + ["stop", "model-control"], check=True)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("options")
    options = json.loads(parser.parse_args().options)
    print(json.dumps(provision(options)))


if __name__ == "__main__":
    main()
