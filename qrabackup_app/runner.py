"""CommandRunner — a QProcess wrapper that streams output through a parser.

This is the only Qt-dependent piece of the run pipeline; the argv/env it runs
and the parsing of its output both live in the pure ``commands``/``parsers``
modules.
"""
from __future__ import annotations

from typing import Dict, List, Optional

from PyQt5 import QtCore

from .parsers import Emit, PlainProgressParser


class CaptureRunner(QtCore.QObject):
    """Runs one subprocess to completion and captures its full stdout/stderr,
    without blocking the GUI thread (unlike subprocess.run). Channels stay
    separate so JSON on stdout remains parseable. ``done(rc, stdout, stderr)``
    fires exactly once per start(): rc 127 = program missing, 124 = timed out,
    130 = killed/cancelled."""

    done = QtCore.pyqtSignal(int, str, str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.proc = QtCore.QProcess(self)
        self.proc.finished.connect(self._on_finished)
        self._timer = QtCore.QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.timeout.connect(self._on_timeout)
        self._timeout_s = 0
        self._timed_out = False

    def running(self) -> bool:
        return self.proc.state() != QtCore.QProcess.NotRunning

    def start(self, program: str, args: List[str],
              env: Optional[Dict[str, str]] = None, timeout_ms: int = 120000):
        if self.running():
            return
        self._timed_out = False
        self._timeout_s = max(1, timeout_ms // 1000)

        penv = QtCore.QProcessEnvironment.systemEnvironment()
        for k, v in (env or {}).items():
            penv.insert(k, str(v))
        self.proc.setProcessEnvironment(penv)

        program_path = QtCore.QStandardPaths.findExecutable(program)
        if not program_path:
            # Deliver asynchronously so the caller finishes wiring up first.
            QtCore.QTimer.singleShot(
                0, lambda: self.done.emit(127, "", f"{program} not found in PATH"))
            return
        self._timer.start(timeout_ms)
        self.proc.start(program_path, args)

    def cancel(self):
        if self.running():
            self.proc.kill()

    def _on_timeout(self):
        self._timed_out = True
        self.cancel()

    def _on_finished(self, code: int, status: QtCore.QProcess.ExitStatus):
        self._timer.stop()
        out = bytes(self.proc.readAllStandardOutput()).decode(errors="replace")
        err = bytes(self.proc.readAllStandardError()).decode(errors="replace")
        if self._timed_out:
            self.done.emit(124, out, f"timed out after {self._timeout_s}s")
        elif status == QtCore.QProcess.CrashExit:
            self.done.emit(130, out, err.strip() or "process was killed")
        else:
            self.done.emit(code, out, err)


class CommandRunner(QtCore.QObject):
    started = QtCore.pyqtSignal()
    line = QtCore.pyqtSignal(str)
    error_line = QtCore.pyqtSignal(str)
    finished = QtCore.pyqtSignal(int)  # exit code
    progress = QtCore.pyqtSignal(int)  # 0..100
    progress_line = QtCore.pyqtSignal(str)  # transient in-place progress text
    state_changed = QtCore.pyqtSignal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.proc = QtCore.QProcess(self)
        self.proc.setProcessChannelMode(QtCore.QProcess.MergedChannels)

        self.proc.readyReadStandardOutput.connect(self._read_stdout)
        self.proc.readyReadStandardError.connect(self._read_stderr)
        self.proc.started.connect(self.started)
        self.proc.stateChanged.connect(self._on_state)
        self.proc.finished.connect(self._on_finished)

        self._buf = b""
        self._parser = PlainProgressParser()
        self._last_percent = -1

    def start(self, program: str, args: List[str],
              env: Optional[Dict[str, str]] = None, parser=None):
        self._buf = b""
        self._last_percent = -1
        self._parser = parser or PlainProgressParser()
        self.progress.emit(0)

        penv = QtCore.QProcessEnvironment.systemEnvironment()
        for k, v in (env or {}).items():
            penv.insert(k, str(v))
        self.proc.setProcessEnvironment(penv)

        program_path = QtCore.QStandardPaths.findExecutable(program)
        if not program_path:
            self.error_line.emit(f"Error: {program} not found in PATH.")
            self.finished.emit(127)
            return
        self.proc.start(program_path, args)

    def stop(self):
        if self.proc.state() != QtCore.QProcess.NotRunning:
            # Ask the process to stop cleanly so it can clean up (rsync temp
            # files, restic locks), then force-kill if it doesn't exit promptly.
            self.proc.terminate()
            if not self.proc.waitForFinished(3000):
                self.proc.kill()

    def _route(self, emit: Emit):
        if emit.percent is not None:
            pct = max(0, min(100, emit.percent))
            if pct != self._last_percent:
                self._last_percent = pct
                self.progress.emit(pct)
        if emit.status:
            self.progress_line.emit(emit.status)
        if emit.log is not None:
            self.line.emit(emit.log)
        if emit.error:
            self.error_line.emit(emit.error)

    def _read_stdout(self):
        data = self.proc.readAllStandardOutput()
        if not data:
            return
        self._buf += bytes(data)
        # Output may redraw a status line in place using carriage returns and
        # only emit a newline at the end (rsync --info=progress2; restic check).
        # Treat both CR and LF as delimiters so progress is parsed live. The
        # parser decides whether a segment is a log line or a transient status.
        self._buf = self._buf.replace(b"\r\n", b"\n")
        while True:
            positions = [p for p in (self._buf.find(b"\n"), self._buf.find(b"\r")) if p != -1]
            if not positions:
                break
            cut = min(positions)
            seg = self._buf[:cut]
            term = self._buf[cut:cut + 1]
            self._buf = self._buf[cut + 1:]
            text = seg.decode(errors="replace")
            self._route(self._parser.feed(text, term == b"\n"))

    def _read_stderr(self):
        # With MergedChannels this rarely fires, but handle it defensively.
        data = self.proc.readAllStandardError()
        if not data:
            return
        for raw in bytes(data).split(b"\n"):
            if not raw:
                continue
            self._route(self._parser.feed(raw.decode(errors="replace"), True))

    def _on_state(self, state: QtCore.QProcess.ProcessState):
        mapping = {
            QtCore.QProcess.NotRunning: "NotRunning",
            QtCore.QProcess.Starting: "Starting",
            QtCore.QProcess.Running: "Running",
        }
        self.state_changed.emit(mapping.get(state, str(int(state))))

    def _on_finished(self, code: int, status: QtCore.QProcess.ExitStatus):
        if self._buf:
            try:
                self._route(self._parser.feed(self._buf.decode(errors="replace"), True))
            finally:
                self._buf = b""
        self.progress.emit(100 if code == 0 else max(self._last_percent, 0))
        self.finished.emit(code)
