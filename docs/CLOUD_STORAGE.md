# Recordings on Google Drive or OneDrive

A recording can live on a mounted cloud filesystem. Pass its mounted path to
`run` or `batch`; it is read in place rather than copied into the session. You do
not need to download the video into the client workspace first. Network reads
are still necessary, and the work directory stores extracted audio, screenshots,
review clips, and output files.

## Configure and mount

Install [rclone](https://rclone.org/install/) and the mount support required by
its [platform guide](https://rclone.org/commands/rclone_mount/): FUSE on Linux,
WinFsp on Windows, or a supported mount driver on macOS. Configure a remote using
`rclone config`, choosing Google Drive or Microsoft OneDrive and completing that
provider's authentication. The name is your choice; these examples use `gdrive`
and `onedrive`. See rclone's [Drive](https://rclone.org/drive/) and
[OneDrive](https://rclone.org/onedrive/) instructions.

The application reuses rclone's existing configuration. It does not store provider
tokens or sign into a cloud account. Keep rclone configuration outside Git.

In one terminal on Linux/macOS:

```sh
please-transcribe cloud mount gdrive:Recordings ~/mnt/recordings
# Or use onedrive:Recordings with your chosen empty mount directory.
```

Keep that terminal open. In a second terminal:

```sh
please-transcribe run "$HOME/mnt/recordings/lecture.mp4" --work work/lecture
```

The mount helper runs `rclone mount` with `--read-only --vfs-cache-mode off`.
The cloud source is not modified and video reads are not buffered into an rclone
VFS disk cache. See [rclone's cache modes](https://rclone.org/commands/rclone_mount/#vfs-file-caching).
Press Ctrl+C in the mount terminal to stop it after processing. Keep the mount
available for later dense-frame extraction and source verification.

An already configured OS/cloud mount also works if it exposes readable, seekable
files. Its cache behavior is controlled by that mount software. A browser sharing
link is not a filesystem path; use the mount for authenticated private files.

## Windows

Install WinFsp and configure rclone first. In one PowerShell terminal:

```powershell
please-transcribe cloud mount onedrive:Recordings X:
```

Then, in another terminal:

```powershell
please-transcribe run 'X:\lecture.mp4' --work work/lecture
```

Use an available drive letter or an appropriate mount directory as described in
rclone's Windows guide. The helper does not install drivers or alter an existing
mount. A nonempty directory is rejected rather than hiding its contents.

## Containers and shared ASR

Mount the cloud folder where the CLI can actually read it. For a compatible host
mount, bind it into the client container read-only, along with the work directory
and private ASR profile:

```sh
docker run --rm \
  -v "$HOME/mnt/recordings:/cloud:ro" \
  -v "$PWD/work:/work" \
  -v "$HOME/.config/please-transcribe:/config:ro" \
  please-transcribe-client run /cloud/lecture.mp4 \
  --work /work/lecture --connection /config/client.json
```

Use an endpoint reachable from that container and attach its Docker network if
necessary. The host mount must be shared with Docker; confirm it can be read from
inside the container. Mounting inside an existing development container instead
requires its own mount-driver/device permissions. This application does not grant
privileged container access automatically.

Only the extracted audio is uploaded to the ASR service. The ASR server does not
need the client's cloud credentials. Screen extraction and exports use the client.
You may choose a separate writable mounted folder for `--work`, but it must support
file locking and atomic file operations. A local work directory is the default.

## Network and verification behavior

Source hashing, audio extraction, and video decoding make separate reads; with
VFS disk caching off, this can mean multiple network passes over a long recording.
Input preparation emits progress heartbeats while hashing. Resuming validates the
source and reuses completed stages; it still requires source access. If the source
changed, use a new session. If the mount disconnected, reconnect it at the same
path or supply the relocated source to `inspect frames --source`.

The code and tests exercise mounted-path intake without an intake copy, read-only
mount command construction, and media processing through a read-only Docker bind
mount. Live Google/Microsoft authentication and OS mount drivers require the
operator's account and setup; they are not exercised by the credential-free tests.
