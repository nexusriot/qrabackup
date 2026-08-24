"""Settings persistence — pure logic + atomic file write. No Qt.

The on-disk format is ``settings.json`` under ``$XDG_CONFIG_HOME`` (or
``~/.config``). Restic secrets live inline, so the file is written ``chmod
0600``. A pre-v2 file (no ``version`` key) is backed up once on first load.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from .model import Job

SETTINGS_VERSION = 2


def config_file_path() -> Path:
    """Path to settings.json honoring XDG_CONFIG_HOME (creates the dir)."""
    xdg = os.environ.get("XDG_CONFIG_HOME")
    base = Path(xdg) if xdg else Path.home() / ".config"
    cfg_dir = base / "qrabackup"
    cfg_dir.mkdir(parents=True, exist_ok=True)
    return cfg_dir / "settings.json"


def is_legacy(data: Dict[str, Any]) -> bool:
    """True if the loaded data predates the current schema version."""
    return data.get("version") != SETTINGS_VERSION


def parse_settings_text(raw: str) -> Dict[str, Any]:
    """Parse settings.json text. Raises ValueError for invalid JSON or a
    non-object root (callers back the file up before it can be overwritten)."""
    data = json.loads(raw)
    if not isinstance(data, dict):
        raise ValueError("settings root is not a JSON object")
    return data


def jobs_from_data(data: Dict[str, Any]) -> List[Job]:
    return [Job.from_dict(j) for j in data.get("jobs", [])]


def window_geometry_from_data(data: Dict[str, Any]) -> Optional[str]:
    """The base64-encoded Qt geometry blob (position/size/maximized state), or
    None. Produced/consumed by the UI via QWidget.saveGeometry/restoreGeometry;
    stored here as an opaque string so this module stays Qt-free."""
    g = data.get("window", {}).get("geometry")
    return g if isinstance(g, str) and g else None


def window_size_from_data(data: Dict[str, Any]) -> Optional[Tuple[int, int]]:
    """Legacy width/height, read only as a fallback for pre-geometry configs."""
    size = data.get("window", {}).get("size")
    if isinstance(size, (list, tuple)) and len(size) == 2:
        try:
            return int(size[0]), int(size[1])
        except (TypeError, ValueError):
            return None
    return None


def build_settings_dict(jobs: List[Job],
                        window: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """Serialize jobs plus an optional window dict (e.g. {"geometry": "<b64>"})."""
    data: Dict[str, Any] = {
        "version": SETTINGS_VERSION,
        "jobs": [j.to_dict() for j in jobs],
    }
    if window:
        data["window"] = dict(window)
    return data


def write_settings(path: Path, data: Dict[str, Any]) -> None:
    """Atomically write settings (tmp file + replace) with owner-only perms.
    Secrets live here, so the tmp file is created 0600 from the start — never
    world-readable, not even between creation and a later chmod."""
    path = Path(path)
    tmp = path.with_suffix(".tmp")
    fd = os.open(str(tmp), os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    try:
        os.fchmod(fd, 0o600)  # normalize a pre-existing/umask-restricted tmp
    except OSError:
        pass
    with os.fdopen(fd, "w") as f:
        f.write(json.dumps(data, indent=2))
    tmp.replace(path)
    try:
        os.chmod(path, 0o600)
    except OSError:
        pass


def _write_backup_once(path: Path, raw: str, suffix: str) -> Optional[Path]:
    path = Path(path)
    bak = path.with_name(path.name + suffix)
    if bak.exists():
        return None
    try:
        bak.write_text(raw)
        os.chmod(bak, 0o600)  # backups carry the same secrets
        return bak
    except OSError:
        return None


def write_legacy_backup(path: Path, raw: str) -> Optional[Path]:
    """Write a one-time ``.v1.bak`` of the pre-migration file. Returns the
    backup path if created, else None (already present / write failed)."""
    return _write_backup_once(path, raw, ".v1.bak")


def write_corrupt_backup(path: Path, raw: str) -> Optional[Path]:
    """Preserve an unparseable settings file as ``.corrupt.bak`` before the app
    inevitably rewrites settings.json (auto-save / save-on-close would silently
    destroy a possibly hand-recoverable file). One-time, like the v1 backup."""
    return _write_backup_once(path, raw, ".corrupt.bak")
