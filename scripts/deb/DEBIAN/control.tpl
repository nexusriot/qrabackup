Package: qrabackup
Version: @VERSION@
Section: utils
Priority: optional
Architecture: @ARCH@
Installed-Size: @SIZE@
Recommends: rsync, restic
Maintainer: Vlad Ananyev <vlananyev@gmail.com>
Homepage: https://github.com/nexusriot/qrabackup
Description: rsync + restic/S3 backup GUI
 Qrabackup is a small desktop backup tool with per-profile engines:
 rsync mirrors sources to a local or mounted path, and restic makes
 encrypted, deduplicated backups to S3 and S3-compatible stores
 (MinIO, Wasabi, Backblaze-S3). Profiles carry sources, excludes,
 retention policy and per-profile last-run status; restic actions
 (test connection, init, snapshots/restore, check, unlock, forget +
 prune with dry-run preview, stats) are one click away, with live
 progress and a masked command preview.
 .
 rsync and/or restic must be installed separately to provide the
 backup engines.
