"""Pure formatting helpers — no Qt, no I/O."""
from __future__ import annotations

from typing import Any, Dict


def human_bytes(n: Any) -> str:
    try:
        n = float(n)
    except (TypeError, ValueError):
        return str(n)
    if abs(n) < 1024:
        return f"{int(n)} B"
    n /= 1024.0
    for unit in ("KiB", "MiB", "GiB", "TiB", "PiB"):
        if abs(n) < 1024:
            return f"{n:.1f} {unit}"
        n /= 1024.0
    return f"{n:.1f} EiB"


def fmt_eta(seconds: Any) -> str:
    try:
        s = int(seconds)
    except (TypeError, ValueError):
        return "?"
    h, rem = divmod(s, 3600)
    m, sec = divmod(rem, 60)
    if h:
        return f"{h}:{m:02d}:{sec:02d}"
    return f"{m}:{sec:02d}"


def fmt_duration(seconds: Any) -> str:
    """Compact human duration: '5s', '1m15s', '1h03m'."""
    try:
        s = float(seconds)
    except (TypeError, ValueError):
        return "?"
    if s < 0:
        return "?"
    if s < 60:
        return f"{s:.0f}s"
    m, sec = divmod(int(round(s)), 60)
    h, m = divmod(m, 60)
    if h:
        return f"{h}h{m:02d}m"
    return f"{m}m{sec:02d}s"


def format_last_run(lr: Any) -> str:
    """Short status suffix for the profile list ('' when never run). Duck-typed
    against model.LastRun so this module stays dependency-free."""
    if not lr or not getattr(lr, "when", ""):
        return ""
    when = str(lr.when)[:16].replace("T", " ")
    if getattr(lr, "exit_code", 0) == 0:
        return f"✓ {when} · {fmt_duration(getattr(lr, 'duration_s', 0.0))}"
    return f"✗ exit {lr.exit_code} · {when}"


def format_restic_status(msg: Dict[str, Any]) -> str:
    """One-line status from a restic backup --json ``status`` message."""
    pct = float(msg.get("percent_done", 0.0)) * 100.0
    parts = [f"{pct:.0f}%"]
    tb = msg.get("total_bytes", 0)
    if tb:
        parts.append(f"{human_bytes(msg.get('bytes_done', 0))}/{human_bytes(tb)}")
    tf = msg.get("total_files", 0)
    if tf:
        parts.append(f"{msg.get('files_done', 0)}/{tf} files")
    eta = msg.get("seconds_remaining")
    if eta:
        parts.append(f"ETA {fmt_eta(eta)}")
    return "  ·  ".join(parts)


def format_restic_summary(msg: Dict[str, Any]) -> str:
    """Final log line from a restic backup --json ``summary`` message."""
    snap = str(msg.get("snapshot_id", ""))[:8] or "(unknown)"
    return (
        f"Snapshot {snap} saved — files: {msg.get('files_new', 0)} new, "
        f"{msg.get('files_changed', 0)} changed, {msg.get('files_unmodified', 0)} unmodified; "
        f"added {human_bytes(msg.get('data_added', 0))} "
        f"(processed {human_bytes(msg.get('total_bytes_processed', 0))}) "
        f"in {float(msg.get('total_duration', 0)):.1f}s"
    )
