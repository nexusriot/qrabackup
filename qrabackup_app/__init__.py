"""Qrabackup — a small rsync + restic/S3 backup GUI for Linux.

The package separates pure, testable logic from the Qt UI:

- ``formats``   — human-readable size/ETA + restic message formatting (pure)
- ``model``     — data model: RsyncOptions, ResticRetention, ResticConfig, Job (pure)
- ``settings``  — load/serialize ~/.config/qrabackup/settings.json (pure)
- ``parsers``   — map subprocess output to progress/log emissions (pure)
- ``commands``  — build rsync/restic argv + run-step plans (pure)
- ``runner``    — CommandRunner: QProcess wrapper (Qt)
- ``ui``        — SnapshotsDialog, MainWindow, main() (Qt)

Importing ``formats``/``model``/``settings``/``parsers``/``commands`` does NOT
import PyQt5, so the logic can be unit-tested without a display.
"""

__version__ = "0.5.0"
