"""Progress parsers — pure logic, no Qt.

A parser maps one output segment to an ``Emit`` describing what the runner
should do with it (update the progress bar, show a transient status line,
append a log line, or report an error). This lets rsync's ``%``/``to-chk``
parsing and restic's ``--json`` parsing coexist behind one runner.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Optional

from .formats import format_restic_status, format_restic_summary


@dataclass
class Emit:
    percent: Optional[int] = None  # 0..100 -> progress bar
    status: Optional[str] = None   # transient one-liner -> status bar
    log: Optional[str] = None      # permanent line -> log pane
    error: Optional[str] = None    # error line -> log pane


class PlainProgressParser:
    """No percent parsing: newline-terminated text goes to the log, in-place
    (CR) redraws go to the status bar. Used for restic init/snapshots/check."""

    def feed(self, text: str, final_newline: bool) -> Emit:
        if final_newline:
            return Emit(log=text)
        s = text.strip()
        return Emit(status=s or None)


class RsyncProgressParser:
    _re_percent = re.compile(r"(\d{1,3})%")
    _re_tochk = re.compile(r"(?:ir-chk|to-chk|to-check)=(\d+)/(\d+)")

    def _percent(self, line: str) -> Optional[int]:
        m = self._re_percent.search(line)
        if m:
            return max(0, min(100, int(m.group(1))))
        m2 = self._re_tochk.search(line)
        if m2:
            left, total = int(m2.group(1)), int(m2.group(2))
            if total > 0:
                return int(((total - left) / total) * 100)
        return None

    def feed(self, text: str, final_newline: bool) -> Emit:
        pct = self._percent(text)
        if final_newline:
            return Emit(percent=pct, log=text)
        s = text.strip()
        return Emit(percent=pct, status=s or None)


class ResticJsonProgressParser:
    """Parses `restic backup --json` newline-delimited JSON messages."""

    def feed(self, text: str, final_newline: bool) -> Emit:
        t = text.strip()
        if not t:
            return Emit()
        try:
            msg = json.loads(t)
        except (ValueError, json.JSONDecodeError):
            # restic warnings / stderr passthrough are plain text.
            if final_newline:
                return Emit(log=text)
            return Emit(status=t)
        if not isinstance(msg, dict):
            return Emit()
        mt = msg.get("message_type")
        if mt == "status":
            return Emit(
                percent=int(round(float(msg.get("percent_done", 0.0)) * 100)),
                status=format_restic_status(msg),
            )
        if mt == "summary":
            return Emit(percent=100, log=format_restic_summary(msg))
        if mt == "error":
            err = msg.get("error")
            detail = err.get("message") if isinstance(err, dict) else (err or t)
            ctx = " ".join(x for x in (msg.get("during", ""), msg.get("item", "")) if x).strip()
            return Emit(error=f"restic error: {detail}" + (f" ({ctx})" if ctx else ""))
        # verbose_status and unknown types are ignored.
        return Emit()
