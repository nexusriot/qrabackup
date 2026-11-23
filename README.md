# Qrabackup – rsync + restic/S3 Backup GUI

Qrabackup is a small, no-nonsense backup helper built with **PyQt5**. Each
backup *profile* (called a “location”) uses one of two engines:

- **rsync** — mirror folders to a local or mounted destination
- **restic** — encrypted, deduplicated snapshots in S3 / S3-compatible storage

Each profile has its own sources, exclude patterns and engine options.
Profiles are saved in `settings.json` (see [Settings](#settings)) and loaded
automatically on start.

---

## Features

-  **Multiple profiles** (“Backup Locations”) in a list on the left  
-  Each profile has:
  - One or more **Sources**
  - One **Destination**
  - **Exclude patterns** (one per line)
  - Per-profile **rsync options** (archive, verbose, compress, delete, dry-run, etc.)
-  **Run Selected** – run the currently selected profile  
-  **Run All** – run all profiles sequentially  
-  **Last-run status per profile** – ✓/✗, time, and duration shown in the
   profile list and remembered across restarts  
-  **Auto-save settings** on any change and on exit  
-  **Command preview** – shows the exact `rsync` command being executed  
-  **Live log output** and **progress bar** based on rsync output (`--info=progress2`)  
-  Settings stored in a single JSON file (see [Settings](#settings))  

---

## Requirements

- **Linux** (primary target). Windows x64 builds are produced too; restic
  works there, rsync needs a Windows port (e.g. cwRsync) in `PATH`.
- **Python** 3.8+ and **PyQt5** (only when running from source — the packaged
  builds bundle both)
- **rsync** in `PATH` for rsync profiles
- **restic** in `PATH` for restic profiles

Install PyQt5 (for example):

```bash
pip install PyQt5
```

---

## Backends (engines)

Each profile picks an **engine**:

### rsync (default)
Mirrors sources to a local or mounted destination path. This is the original
behaviour — unchanged.

### restic (S3, encrypted)
Backs up sources to **S3 or any S3-compatible store** (MinIO, Wasabi,
Backblaze-S3, …) using [restic](https://restic.net): client-side encryption,
deduplication, and snapshot history.

Requires `restic` in `PATH`.

Per restic profile you set:

- **Bucket / Region / Endpoint / Prefix** — the repository URL is built for you:
  - AWS: `s3:s3.<region>.amazonaws.com/<bucket>/<prefix>`
  - S3-compatible: set **Endpoint** to e.g. `https://minio.local:9000`
  - Advanced: paste a raw `s3:…` repository to override the fields.
- **Repo password** — the encryption key. **Keep it safe: without it the backup
  cannot be decrypted.**
- **S3 access key / secret key**.
- **Retention** (`keep-last/daily/weekly/monthly/yearly` + `prune`) and **Tags**.
  When retention is enabled, a `forget --prune` step runs automatically after
  each successful backup (scoped to the profile's tags).

**Run Selected / Run All** perform the backup, with a live progress bar driven
by `restic backup --json`.

Action buttons (restic profiles):

- **Test** — verify the repository is reachable and the credentials/password
  work (`restic cat config`); failures come with a plain-language diagnosis
  (wrong password, bad S3 keys, missing repo, stale lock, unreachable endpoint).
- **Init repo** — create a new encrypted repository (do this once; confirmed).
- **Snapshots…** — browse snapshots in a table; from there **Restore selected…**
  (warns if the target folder is non-empty) or **Forget selected**.
- **Check** — verify repository integrity.
- **Unlock** — remove stale repository locks left by interrupted runs.
- **Forget + Prune…** — apply the retention policy and reclaim space. Shows a
  `--dry-run` preview and asks for confirmation before deleting anything.
- **Stats** — logical size, raw stored size, and dedup/compression saving.
- **Export…** (next to the password field) — save the repository URL, password
  and S3 keys to a plaintext `0600` file as an offline recovery escrow.

Snapshots, stats, and dry-run previews run in the background — the window stays
responsive and **Stop** cancels them.

> **Security note:** credentials and the repo password are stored in plaintext
> in `~/.config/qrabackup/settings.json` (written `chmod 0600`, and masked in
> the on-screen command preview/log). **Do not commit or sync this file.**

See [`docs/DESIGN-restic-s3.md`](docs/DESIGN-restic-s3.md) for the full design.

---

## Settings

Settings are stored in `settings.json` under:

- Linux: `$XDG_CONFIG_HOME/qrabackup/` (default `~/.config/qrabackup/`)
- Windows: `%APPDATA%\qrabackup\` (`$XDG_CONFIG_HOME` still wins if set)

On first run with an older config, it is migrated to the new format and the
original is preserved as `settings.json.v1.bak`. A file that cannot be parsed —
invalid JSON or a broken structure such as `"jobs"` not being a list — is
preserved as `settings.json.corrupt.bak` (mode `0600`) before the app starts
with a default profile. Fields with the wrong type (for example `null` where an
object is expected) are replaced by defaults.

The window geometry (position, size, and maximized state) is remembered under
`window.geometry` and restored on the next launch. Older configs that only
stored `window.size` are still honored as a fallback.

---

## Project layout

`qrabackup.py` is a thin launcher; the implementation is the `qrabackup_app`
package, which keeps the **pure, testable logic separate from the Qt UI**:

| Module | Qt? | Responsibility |
|---|---|---|
| `qrabackup_app/formats.py`  | no  | human-readable sizes/ETA, restic message formatting |
| `qrabackup_app/model.py`    | no  | data model: `RsyncOptions`, `ResticRetention`, `ResticConfig`, `Job` |
| `qrabackup_app/settings.py` | no  | load / serialize / atomically write `settings.json` |
| `qrabackup_app/parsers.py`  | no  | map subprocess output → progress / log / error emissions |
| `qrabackup_app/commands.py` | no  | build rsync/restic argv, run-step plans, command preview |
| `qrabackup_app/runner.py`   | yes | `CommandRunner` (streams output) and `CaptureRunner` (captures output without blocking) — `QProcess` wrappers |
| `qrabackup_app/ui.py`       | yes | `SnapshotsDialog`, `MainWindow`, `main()` |

Importing the no-Qt modules does **not** import PyQt5, so the logic is unit-
tested without a display.

Run it either way:

```bash
python3 qrabackup.py
# or
python3 -m qrabackup_app
```

---

## Tests

Unit tests cover the pure logic plus offscreen UI regression tests (no
display / network needed):

```bash
make test
# or directly:
python3 -m unittest discover -s tests
# or, if you have pytest:
pytest
```

## Building

### Release builds (hermetic, Docker)

`packaging/build.sh` builds every release artifact in pinned Docker containers.
The base images are pinned by digest, apt installs from a fixed snapshot date,
Python build dependencies are pinned, and the build itself runs with
`--network none` against a read-only checkout:

```bash
make dist                # all targets below
make dist-linux-amd64    # dist/linux-amd64/: binary, .deb, .rpm
make dist-linux-arm64    # dist/linux-arm64/: binary, .deb, .rpm (aarch64)
make dist-windows        # dist/windows-x64/: qrabackup.exe + NSIS setup.exe
```

Checksums of all artifacts are written to `dist/SHA256SUMS`. See
[`docs/PACKAGING.md`](docs/PACKAGING.md) for details, supported distributions,
and cross-building arm64 on an x86 host (QEMU).

### Local builds

The Makefile wraps `scripts/build.sh` / `scripts/build-deb.sh` (a project-local
`.venv` is provisioned automatically; PyInstaller builds for the host arch):

```bash
make            # standalone binary -> dist/qrabackup
make deb        # Debian package    -> dist/qrabackup_<version>_<arch>.deb
make run        # run from source in the local .venv
make test       # unit test suite (offscreen)
make clean      # remove build/, dist/ and .venv
```

`VERSION` defaults to `git describe`, falling back to the package's
`__version__`; override with `make VERSION=1.2.3 deb`.

