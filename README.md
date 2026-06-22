# Qrabackup – Simple Rsync Backup GUI for Linux

Qrabackup is a small, no-nonsense backup helper for Linux built with **PyQt5** and **rsync**.

It lets you define multiple backup *profiles* (called “locations”) with their own:

- Source folders
- Destination folder
- Exclude patterns
- Per-job rsync options

Profiles are saved under `~/.config/qrabackup/settings.json` (XDG-aware) and are loaded automatically on start.

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
-  **Auto-save settings** on any change and on exit  
-  **Command preview** – shows the exact `rsync` command being executed  
-  **Live log output** and **progress bar** based on rsync output (`--info=progress2`)  
-  Settings stored in a single JSON file: `~/.config/qrabackup/settings.json`  
-  Honors `$XDG_CONFIG_HOME` if set  

---

## Requirements

- **Linux** (tested/targeted)
- **Python** 3.8+
- **PyQt5**
- **rsync** available in `PATH`

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

- **Init repo** — create a new encrypted repository (do this once; confirmed).
- **Snapshots…** — browse snapshots in a table; from there **Restore selected…**
  (warns if the target folder is non-empty) or **Forget selected**.
- **Check** — verify repository integrity.
- **Forget + Prune…** — apply the retention policy and reclaim space. Shows a
  `--dry-run` preview and asks for confirmation before deleting anything.
- **Stats** — logical size, raw stored size, and dedup/compression saving.

> **Security note:** credentials and the repo password are stored in plaintext
> in `~/.config/qrabackup/settings.json` (written `chmod 0600`, and masked in
> the on-screen command preview/log). **Do not commit or sync this file.**

See [`docs/DESIGN-restic-s3.md`](docs/DESIGN-restic-s3.md) for the full design.

---

## Settings

Settings are stored in `~/.config/qrabackup/settings.json` (honors
`$XDG_CONFIG_HOME`). On first run with an older config, it is migrated to the
new format and the original is preserved as `settings.json.v1.bak`.

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
| `qrabackup_app/runner.py`   | yes | `CommandRunner` — `QProcess` wrapper |
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

Unit tests cover the pure logic (no Qt / display / network needed):

```bash
python3 -m unittest discover -s tests
# or, if you have pytest:
pytest
```

