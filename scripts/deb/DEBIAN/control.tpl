Package: qrabackup
Version: @VERSION@
Section: utils
Priority: optional
Architecture: @ARCH@
Installed-Size: @SIZE@
Depends: libc6 (>= 2.35), libgl1, libegl1, libfontconfig1, libfreetype6,
 libdbus-1-3, libx11-xcb1, libxcb1, libxkbcommon0, libxkbcommon-x11-0,
 libxcb-icccm4, libxcb-image0, libxcb-keysyms1, libxcb-randr0,
 libxcb-render-util0, libxcb-shape0, libxcb-xfixes0, libxcb-xinerama0,
 libxcb-xkb1
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
