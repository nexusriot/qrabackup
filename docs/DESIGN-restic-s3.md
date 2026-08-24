# Design: S3 + restic backup engine for Qrabackup

Status: **Draft / for review**
Author: design pass before implementation
Scope: add encrypted, deduplicated cloud backups via `restic` (with S3 as the
primary backend) to the existing rsync-based Qrabackup GUI.

---

## 1. Goal

Today Qrabackup is a PyQt5 front-end that runs `rsync` from a set of *sources*
to one *local destination path*, per profile. It is great for mirroring to a
mounted disk, but it cannot back up to object storage, has no encryption, no
deduplication, and no snapshot history.

We want to let a profile back up to **S3 (and any S3-compatible store) using
restic**, while keeping the existing rsync behaviour intact.

`restic` gives us for free: client-side encryption, content-addressed dedup,
incremental snapshots, retention/prune, integrity checks, and restore.

### Non-goals (this iteration)
- Scheduling / cron (Qrabackup is run-on-demand; scheduling can come later).
- A general rclone backend picker (B2/GDrive/etc.). We design so it *can* be
  added, but ship S3 first.
- Mount/browse of restic snapshots (`restic mount` needs FUSE) — restore is
  whole-snapshot or path-scoped for v1.

---

## 2. Guiding principles

1. **Coexist, don't replace.** rsync profiles keep working byte-for-byte. We
   add an `engine` selector to each profile; restic is just a second engine.
2. **Secrets never land in `settings.json`.** The repo password and S3 secret
   key are stored in the OS keyring (or referenced from a file/env), not in the
   plaintext config. See §5.
3. **Reuse the process plumbing.** The existing `RsyncRunner` (a `QProcess`
   wrapper with line/progress parsing) is generalised, not duplicated.
4. **Make destructive actions loud.** `forget --prune`, `restore` over existing
   files, and repo `init` all confirm first, consistent with the current
   `--delete` confirmation.

---

## 3. Current architecture (recap)

```
MainWindow
 ├─ jobs: List[Job]                 # profiles, persisted to settings.json
 ├─ RsyncRunner (QProcess wrapper)  # runs one rsync, parses stdout for progress
 └─ UI: [profiles list] | [sources, destination, options, excludes, log]

Job = { name, sources[], destination, excludes[], options:JobOptions }
JobOptions = rsync flags (archive, verbose, compress, delete, preserve, dry_run, progress)
settings.json: { jobs:[...], window:{size} }   @ ~/.config/qrabackup/
```

Key files: [qrabackup.py](../qrabackup.py) — `Job`, `JobOptions`, `RsyncRunner`,
`MainWindow`.

---

## 4. Proposed architecture

### 4.1 Engine abstraction

Introduce an engine concept. Each profile declares `engine: "rsync" | "restic"`.

```
Job
 ├─ name
 ├─ engine            # "rsync" (default) | "restic"
 ├─ sources[]         # shared by both engines
 ├─ excludes[]        # shared by both engines
 ├─ rsync:  RsyncOptions   # existing options, only used when engine=="rsync"
 └─ restic: ResticConfig   # new, only used when engine=="restic"
```

`destination` (existing rsync field) stays for rsync. restic uses
`restic.repository` instead.

### 4.2 The runner becomes generic

`RsyncRunner` already does everything we need (QProcess, merged channels,
CR/LF-aware line splitting, terminate-then-kill stop). We rename/refactor it to
a generic `CommandRunner` that:

- takes `program`, `args`, and an **`env` dict** (restic needs env vars for
  credentials — rsync did not),
- takes an optional **progress parser strategy** so rsync's `%`/`to-chk`
  parsing and restic's `--json` parsing live side by side,
- keeps the existing signals: `started / line / error_line / finished /
  progress / progress_line / state_changed`.

```
CommandRunner
  start(program, args, env: dict[str,str]|None, parser: ProgressParser)

ProgressParser (interface)  -> feed(line:str) -> (pct:int|None, status:str|None)
  ├─ RsyncProgressParser    # current regexes (%  and  to-chk=left/total)
  └─ ResticJsonProgressParser  # parses ND-JSON status/summary messages
```

This is a small, mechanical extraction — the body of `_read_stdout` /
`_update_progress_from_line` moves into the parser.

---

## 5. Secret handling (the important part)

restic needs two kinds of secret per S3 profile:

| Secret | restic env var | Sensitivity |
|---|---|---|
| Repository encryption password | `RESTIC_PASSWORD` (or `_FILE`/`_COMMAND`) | **critical — losing it = unrecoverable backup** |
| S3 access key id | `AWS_ACCESS_KEY_ID` | medium |
| S3 secret access key | `AWS_SECRET_ACCESS_KEY` | high |

Region/endpoint/bucket/prefix are **not** secret and live in `settings.json`.

### Decision (chosen): plaintext JSON in the user config dir

Per the project owner's choice, secrets are stored **inline in the existing
`settings.json`** under each restic profile — no keyring, no external files.
This keeps the "just type it in the GUI and it's saved" UX with zero new
dependencies. We mitigate the obvious risks:

- The settings file is written with **`chmod 0600`** (owner-only) on every save.
- Secret values are **masked** in the command preview and the run log (the
  preview shows `RESTIC_PASSWORD=*** … restic …`), so screenshots / shared logs
  don't leak them.
- At run time we assemble an `env` dict for `CommandRunner` from the stored
  values; the values are never written into argv (so they don't appear in
  `ps`/process listings).

```jsonc
"restic": {
  "bucket":   "my-bucket",
  "region":   "eu-central-1",
  "endpoint": "",                 // blank = AWS; set for MinIO/Wasabi/B2-S3
  "prefix":   "laptop",           // path within the bucket
  "repository": "",               // advanced: raw "s3:…" override; blank = built from the fields above
  "password":          "•stored plaintext•",   // RESTIC_PASSWORD
  "access_key_id":     "AKIA…",                 // AWS_ACCESS_KEY_ID
  "secret_access_key": "•stored plaintext•",    // AWS_SECRET_ACCESS_KEY
  "tags": ["laptop"],
  "retention": { "enabled": false, "keep_last": 0, "keep_daily": 7,
                 "keep_weekly": 4, "keep_monthly": 6, "keep_yearly": 0, "prune": true }
}
```

> ⚠️ **Caveats the user accepted:** anything with read access to the file (or a
> backup/sync of `~/.config`) can read the repo password and S3 keys. Do not
> commit or sync `settings.json`. Because the repo password is the *only* way to
> decrypt the backup, also record it somewhere safe — if this file is lost, the
> backup is unrecoverable. (Keyring / `RESTIC_PASSWORD_FILE` / `AWS_PROFILE`
> remain easy to add later as opt-in sources if the threat model changes.)

---

## 6. restic backend specifics

### Repository URL forms we support
- AWS, region-specific: `s3:s3.<region>.amazonaws.com/<bucket>/<prefix>`
- AWS, default: `s3:s3.amazonaws.com/<bucket>/<prefix>`
- S3-compatible (MinIO/Wasabi/B2): `s3:https://<endpoint>/<bucket>/<prefix>`

The GUI builds this URL from bucket + region + optional endpoint fields, and
also allows pasting a raw `repository` string (advanced).

### Operations (each is a `restic` subprocess via `CommandRunner`)

| Action | Command (env carries creds) | Notes / safety |
|---|---|---|
| **Init** | `restic init` | Only if repo not yet initialised. Confirm. |
| **Backup** | `restic backup --json [--tag T] [--exclude P ...] SRC...` | Primary action. `--json` drives progress. |
| **Snapshots** | `restic snapshots --json` | Populate a snapshot table. |
| **Check** | `restic check` | Integrity verify. |
| **Forget+Prune** | `restic forget --prune --keep-daily N ...` | **Destructive** — confirm; show what would be removed (`--dry-run` first). |
| **Restore** | `restic restore <id> --target <dir> [--include PATH]` | Confirm if target non-empty. |
| **Stats** | `restic stats --json` | Repo size / dedup ratio in status bar. |
| **Unlock** | `restic unlock` | Offer when a `repository is already locked` error is seen. |

### Detecting repo state
Run `restic snapshots` (or `cat config`); exit code / stderr tells us
"repository not initialized" vs locked vs bad-credentials. We map these to
friendly prompts ("Initialise this repository?", "Repo is locked — unlock?").

### Progress from `restic backup --json`
restic emits newline-delimited JSON. We care about:
- `{"message_type":"status", "percent_done":0.42, "bytes_done":..,
  "total_bytes":.., "seconds_remaining":..}` → drive progress bar + status line.
- `{"message_type":"summary", "files_new":.., "data_added":.., ...}` → final log
  line ("Added 1.2 GiB in 312 files, 84 MiB deduped").
Errors (`message_type:"error"`) → error log.

---

## 7. UI changes

Keep the left profiles list as-is. The right pane gets an **engine selector**
at the top and a `QStackedWidget` that swaps the engine-specific editor.

```
[ Engine: ( ) rsync   (•) restic ]      <- new, top of right pane

Sources            [shared widget — unchanged]
Excludes           [shared widget — unchanged]

── if rsync ──────────────────────────────
  Destination + Options (the current checkboxes)   [unchanged]

── if restic ─────────────────────────────
  Repository:  [ bucket ] [ region ] [ endpoint(optional) ]   -> preview URL
  Credentials: [ repo password ••• ] [ test ]
               [ S3 access key ] [ S3 secret ••• ]  (or "use AWS profile ▾")
  Retention:   keep-last[ ] daily[7] weekly[4] monthly[6] yearly[ ]  [x] prune
  Tags:        [ laptop, docs ]
  Actions:     [ Init repo ] [ Snapshots… ] [ Check ] [ Forget + Prune… ] [ Stats ]

Command preview  [secrets masked]                  [shared — unchanged]
[ Preview ] [ Run Selected ] [ Run All ]  ===progress===  [ Stop ]
Log                                                 [shared — unchanged]
```

- **Run Selected / Run All** dispatch on `engine`: rsync → existing path;
  restic → `restic backup`, then `forget --prune` if retention is enabled.
  (Init stays an explicit button — no silent auto-init.)
- **Snapshots…** opens a dialog (id, time, host, tags, paths) with **Restore
  selected…** and **Forget selected**.
- **Restore** confirms when the target dir is non-empty; **Forget + Prune** runs
  a `--dry-run` first and shows the preview before the real prune.

---

## 8. Settings schema & migration

Add a top-level `"version": 2`. The current file has no version (= v1).

Migration v1→v2 (on load): every existing job gets `engine:"rsync"`, its current
fields move under `rsync:{}`, and an empty `restic:{}` is added. rsync profiles
behave exactly as before. Writing is always v2. We keep a `.bak` of the old file
on first migration.

Per-profile **stable `id`** (uuid) is added so keyring entries survive renames.

---

## 9. Safety & error handling

- **Lost-password warning** on first restic profile creation; offer export.
- **Init guard**: never silently init; ask. A typo'd bucket shouldn't create a
  new empty repo unnoticed.
- **Locked repo**: detect, offer `unlock` (warn it's only safe if no other
  backup is running).
- **Prune dry-run preview** before the real prune.
- **Restore target check**: warn if non-empty / refuse to restore over sources
  without explicit confirm.
- **Credential test**: a lightweight `restic cat config` (or `snapshots`) button
  validates creds before a long backup.
- Secrets masked in preview/log; env dict built per-run and not persisted.

---

## 10. Phased implementation plan

**Phase 0 — refactor (no behaviour change). ✅ DONE**
Extracted `CommandRunner` + `RsyncProgressParser` from `RsyncRunner`. Added
`engine`, `id`, and the v1→v2 settings migration. rsync works identically.

**Phase 1 — restic backup happy path. ✅ DONE**
`ResticConfig`, repo-URL builder, engine selector + stacked editor, guarded
`restic init` + `restic backup --json` with `ResticJsonProgressParser`. Secrets
inline (`chmod 0600`, masked). Verified against a local restic repo.

**Phase 2 — manage. ✅ DONE**
Unified run path into a **step queue** (`RunStep`/`_build_run_steps`/
`_run_steps`/`on_step_finished`) so a profile can run multiple sequential steps;
a failed backup skips its own follow-ups but other profiles still run. Added:
Snapshots dialog (`SnapshotsDialog`) with **Restore** (target-not-empty guard)
and **Forget selected**; **Forget + Prune** from the retention policy with a
**dry-run preview** + confirm; **Stats** (logical vs raw-data + dedup %);
auto `forget --prune` after a successful backup when retention is enabled
(refuses to run if the policy has no keep-* values). Quick metadata reads
(snapshots/stats/forget-dry-run) use a blocking `_restic_capture` with a wait
cursor; long ops stream through `CommandRunner`.

**Phase 2.5 — modularize + unit tests. ✅ DONE**
`qrabackup.py` became a thin launcher; code moved into the `qrabackup_app`
package with pure logic separated from Qt:
`formats` / `model` / `settings` / `parsers` / `commands` are **Qt-free** (a
test asserts importing them does not import PyQt5); `runner` / `ui` hold the Qt
pieces. Command argv, run-step plans, the command preview and settings
serialization were lifted out of `MainWindow` into `commands`/`settings` as pure
functions. Stdlib-`unittest` suite under `tests/` (80 tests, no display/network)
covers formats, model, settings, parsers and commands. Run:
`python3 -m unittest discover -s tests`.

**Phase 3 — polish. ✅ MOSTLY DONE (2026-08-24)**
Shipped: credential test (**Test** = `cat config`, with plain-language failure
diagnosis for wrong password / bad S3 keys / missing repo / stale lock /
unreachable endpoint), **Unlock** (stale-lock removal), credentials **Export…**
(plaintext 0600 escrow file). Also shipped alongside: async metadata reads
(snapshots/stats/dry-run via `CaptureRunner`, cancellable, UI never blocks) and
per-profile last-run status (`LastRun` persisted in settings, ✓/✗ + time +
duration in the profile list). Still open: AWS-profile/file credential sources,
screenshots. (`Check` already shipped in Phase 1.)

(Optional Phase 4 — rclone backend for non-S3 targets, reusing the same engine.)

---

## 11. Testing

- **Local S3 via MinIO** in Docker (`minio/minio`) → real S3 API, no cloud cost,
  for init/backup/snapshots/restore/prune round-trips.
- Unit tests for: repo-URL builder, settings v1→v2 migration, restic `--json`
  parser (feed captured fixtures, assert pct/summary), secret-source resolution.
- Manual: rsync regression (a profile from the existing config) + a restic
  round-trip to MinIO, then point at a real bucket once.

---

## 12. Decisions (resolved)

1. **Secret storage** — ✅ plaintext inline in `settings.json` (`chmod 0600`,
   masked in preview/log). No new dependencies. See §5.
2. **S3 scope** — ✅ S3-compatible: bucket + region + optional **endpoint** so
   AWS / MinIO / Wasabi / Backblaze-S3 all work.
3. **Dependencies** — ✅ none added (restic talks S3 directly; no keyring/boto3).
4. **Retention default** — `keep-daily 7 / weekly 4 / monthly 6`, **prune off**
   until the user enables it. Stored in Phase 1; acted on (`forget --prune`) in
   Phase 2.
5. **Next** — ✅ implement Phase 0 (refactor, no behaviour change) + Phase 1
   (restic backup happy path: guarded `init` + `backup --json` with progress).
```
