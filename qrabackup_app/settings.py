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
    """Atomically write settings (tmp file + replace) with owner-only perms."""
    path = Path(path)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, indent=2))
    try:
        os.chmod(tmp, 0o600)
    except OSError:
        pass
    tmp.replace(path)
    try:
        os.chmod(path, 0o600)  # secrets live here — keep it owner-only
    except OSError:
        pass


def write_legacy_backup(path: Path, raw: str) -> Optional[Path]:
    """Write a one-time ``.v1.bak`` of the pre-migration file. Returns the
    backup path if created, else None (already present / write failed)."""
    path = Path(path)
    bak = path.with_name(path.name + ".v1.bak")
    if bak.exists():
        return None
    try:
        bak.write_text(raw)
        return bak
    except OSError:
        return None
