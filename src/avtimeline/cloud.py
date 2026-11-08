"""Read-only cloud mounts using the user's existing rclone configuration."""
from __future__ import annotations

import os
from pathlib import Path
import re
import shutil
import subprocess


def mount(remote: str, mountpoint: str) -> None:
    if (not re.fullmatch(r"[\w][\w .-]*:.*", remote) or "://" in remote
            or re.match(r"^[A-Za-z]:[\\/]", remote)):
        raise ValueError("Use a configured rclone remote such as gdrive:Recordings or onedrive:Recordings")
    executable = shutil.which("rclone")
    if executable is None:
        raise ValueError("Install rclone and your platform's mount driver, then run rclone config; "
                         "see docs/CLOUD_STORAGE.md")
    # A Windows drive-letter mount must not be created as a directory first.
    if os.name == "nt" and re.fullmatch(r"[A-Za-z]:[\\/]?", mountpoint):
        target = mountpoint
    else:
        path = Path(mountpoint).expanduser().absolute()
        path.mkdir(parents=True, exist_ok=True)
        if any(path.iterdir()):
            raise ValueError("Choose an empty mount directory; existing files will not be hidden or deleted")
        target = str(path)
    command = [executable, "mount", remote, target, "--read-only", "--vfs-cache-mode", "off"]
    print("Starting a read-only cloud mount. Keep this terminal open; press Ctrl+C to unmount.", flush=True)
    try:
        subprocess.run(command, check=True)
    except KeyboardInterrupt:
        # The foreground child receives the terminal interrupt too.
        pass
