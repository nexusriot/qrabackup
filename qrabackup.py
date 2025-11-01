#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Simple Backup App for Linux using PyQt5 and rsync.

Features
- Select multiple source folders
- Select destination folder
- Common rsync options: archive, verbose, compress, delete, preserve perms/owner/times
- Exclude patterns (one per line)
- Dry-run mode
- Live log output and a progress bar
- Start / Stop buttons using QProcess (non-blocking)

Dependencies
- Python 3.8+
- PyQt5 (pip install PyQt5)
- rsync available in PATH

Run
  python3 qrabackup.py
"""
from __future__ import annotations

import os
import shlex
import sys
import re
from typing import List

from PyQt5 import QtCore, QtGui, QtWidgets


class RsyncRunner(QtCore.QObject):
    """Wrap QProcess to run rsync and emit signals with stdout/stderr and progress."""

    started = QtCore.pyqtSignal()
    line = QtCore.pyqtSignal(str)
    error_line = QtCore.pyqtSignal(str)
    finished = QtCore.pyqtSignal(int)  # exit code
    progress = QtCore.pyqtSignal(int)  # 0..100
    state_changed = QtCore.pyqtSignal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.proc = QtCore.QProcess(self)
        # Ensure a clean, POSIX-like environment
        env = QtCore.QProcessEnvironment.systemEnvironment()
        self.proc.setProcessEnvironment(env)
        self.proc.setProcessChannelMode(QtCore.QProcess.MergedChannels)

        self.proc.readyReadStandardOutput.connect(self._read_stdout)
        self.proc.readyReadStandardError.connect(self._read_stderr)
        self.proc.started.connect(self.started)
        self.proc.stateChanged.connect(self._on_state)
        self.proc.finished.connect(self._on_finished)

        # Rolling buffer for progress parsing
        self._buf = b""
        self._last_percent = -1

    def start(self, args: List[str]):
        self._buf = b""
        self._last_percent = -1
        self.progress.emit(0)
        # QProcess needs program + arguments
        program = "rsync"
        program_path = QtCore.QStandardPaths.findExecutable(program)
        if not program_path:
            self.error_line.emit("Error: rsync not found in PATH.")
            self.finished.emit(127)
            return

        # Convert list to QStringList
        qargs = [arg for arg in args]
        self.proc.start(program_path, qargs)

    def stop(self):
        if self.proc.state() != QtCore.QProcess.NotRunning:
            self.proc.kill()

    def _read_stdout(self):
        data = self.proc.readAllStandardOutput()
        if not data:
            return
        self._buf += bytes(data)
        # Split into lines while keeping remainder
        lines = self._buf.split(b"\n")
        self._buf = lines[-1]
        for raw in lines[:-1]:
            text = raw.decode(errors="replace")
            self.line.emit(text)
            self._update_progress_from_line(text)

    def _read_stderr(self):
        data = self.proc.readAllStandardError()
        if not data:
            return
        for raw in bytes(data).split(b"\n"):
            if not raw:
                continue
            text = raw.decode(errors="replace")
            # rsync uses stderr for some progress/info; treat similarly
            self.error_line.emit(text)
            self._update_progress_from_line(text)

    def _on_state(self, state: QtCore.QProcess.ProcessState):
        mapping = {
            QtCore.QProcess.NotRunning: "NotRunning",
            QtCore.QProcess.Starting: "Starting",
            QtCore.QProcess.Running: "Running",
        }
        self.state_changed.emit(mapping.get(state, str(int(state))))

    def _on_finished(self, code: int, status: QtCore.QProcess.ExitStatus):
        # Flush any remainder
        if self._buf:
            try:
                text = self._buf.decode(errors="replace")
                self.line.emit(text)
                self._update_progress_from_line(text)
            finally:
                self._buf = b""
        self.progress.emit(100 if code == 0 else max(self._last_percent, 0))
        self.finished.emit(code)

    _re_percent = re.compile(r"\b(\d{1,3})%\b")
    _re_tochk = re.compile(r"to-chk=(\d+)/(\d+)")

    def _update_progress_from_line(self, line: str):
        # Try percent like "  42%" which rsync prints with --info=progress2
        m = self._re_percent.search(line)
        if m:
            pct = int(m.group(1))
            pct = min(max(pct, 0), 100)
            if pct != self._last_percent:
                self._last_percent = pct
                self.progress.emit(pct)
            return
        # Fallback: use to-chk=N/T to estimate
        m2 = self._re_tochk.search(line)
        if m2:
            left, total = int(m2.group(1)), int(m2.group(2))
            if total > 0:
                done = total - left
                pct = int((done / total) * 100)
                if pct != self._last_percent:
                    self._last_percent = pct
                    self.progress.emit(pct)


class MainWindow(QtWidgets.QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Rsync Backup (PyQt)")
        self.resize(980, 680)

        self.runner = RsyncRunner(self)
        self.runner.started.connect(self.on_started)
        self.runner.line.connect(self.append_log)
        self.runner.error_line.connect(self.append_log)
        self.runner.progress.connect(self.on_progress)
        self.runner.finished.connect(self.on_finished)
        self.runner.state_changed.connect(self.on_state)

        self._build_ui()
        self._update_buttons(running=False)

    def _build_ui(self):
        central = QtWidgets.QWidget()
        self.setCentralWidget(central)
        main = QtWidgets.QVBoxLayout(central)

        # Sources + Destination
        src_group = QtWidgets.QGroupBox("Source folders")
        src_layout = QtWidgets.QVBoxLayout(src_group)
        self.src_list = QtWidgets.QListWidget()
        self.src_list.setSelectionMode(QtWidgets.QAbstractItemView.ExtendedSelection)
        btn_row = QtWidgets.QHBoxLayout()
        self.btn_add_src = QtWidgets.QPushButton("Add folder…")
        self.btn_del_src = QtWidgets.QPushButton("Remove selected")
        btn_row.addWidget(self.btn_add_src)
        btn_row.addWidget(self.btn_del_src)
        btn_row.addStretch(1)
        src_layout.addWidget(self.src_list)
        src_layout.addLayout(btn_row)

        dst_group = QtWidgets.QGroupBox("Destination")
        dst_layout = QtWidgets.QHBoxLayout(dst_group)
        self.dst_edit = QtWidgets.QLineEdit()
        self.dst_edit.setPlaceholderText("/path/to/backup/target")
        self.btn_browse_dst = QtWidgets.QPushButton("Browse…")
        dst_layout.addWidget(self.dst_edit, 1)
        dst_layout.addWidget(self.btn_browse_dst)

        main.addWidget(src_group)
        main.addWidget(dst_group)

        # Options
        opt_group = QtWidgets.QGroupBox("Options")
        form = QtWidgets.QGridLayout(opt_group)
        self.chk_archive = QtWidgets.QCheckBox("Archive (-a)")
        self.chk_archive.setChecked(True)
        self.chk_verbose = QtWidgets.QCheckBox("Verbose (-v)")
        self.chk_compress = QtWidgets.QCheckBox("Compress (-z)")
        self.chk_delete = QtWidgets.QCheckBox("Delete extras in dest (--delete)")
        self.chk_preserve = QtWidgets.QCheckBox("Preserve perms/owner/times (-pgoDt)")
        self.chk_dry = QtWidgets.QCheckBox("Dry run (--dry-run)")
        self.chk_progress = QtWidgets.QCheckBox("Show progress (--info=progress2)")
        self.chk_progress.setChecked(True)

        form.addWidget(self.chk_archive, 0, 0)
        form.addWidget(self.chk_verbose, 0, 1)
        form.addWidget(self.chk_compress, 0, 2)
        form.addWidget(self.chk_delete, 1, 0)
        form.addWidget(self.chk_preserve, 1, 1)
        form.addWidget(self.chk_dry, 1, 2)
        form.addWidget(self.chk_progress, 2, 0)

        # Exclude patterns
        ex_label = QtWidgets.QLabel("Exclude patterns (one per line):")
        self.exclude_edit = QtWidgets.QPlainTextEdit()
        self.exclude_edit.setPlaceholderText("*.tmp\n.cache/\nnode_modules/\n.DS_Store\nThumbs.db")
        self.exclude_edit.setFixedHeight(90)

        # Command preview
        self.cmd_preview = QtWidgets.QLineEdit()
        self.cmd_preview.setReadOnly(True)
        self.cmd_preview.setPlaceholderText("rsync command will appear here…")

        main.addWidget(opt_group)
        main.addWidget(ex_label)
        main.addWidget(self.exclude_edit)

        # Controls
        controls = QtWidgets.QHBoxLayout()
        self.btn_build_cmd = QtWidgets.QPushButton("Preview Command")
        self.btn_start = QtWidgets.QPushButton("Start Backup")
        self.btn_stop = QtWidgets.QPushButton("Stop")
        self.progress = QtWidgets.QProgressBar()
        self.progress.setRange(0, 100)
        self.progress.setValue(0)
        controls.addWidget(self.btn_build_cmd)
        controls.addStretch(1)
        controls.addWidget(self.progress, 2)
        controls.addWidget(self.btn_start)
        controls.addWidget(self.btn_stop)

        main.addLayout(controls)
        main.addWidget(self.cmd_preview)

        # Log output
        self.log = QtWidgets.QPlainTextEdit()
        self.log.setReadOnly(True)
        self.log.setWordWrapMode(QtGui.QTextOption.NoWrap)
        main.addWidget(self.log, 10)

        # Connections
        self.btn_add_src.clicked.connect(self.add_source)
        self.btn_del_src.clicked.connect(self.remove_selected_sources)
        self.btn_browse_dst.clicked.connect(self.choose_destination)
        self.btn_build_cmd.clicked.connect(self.update_cmd_preview)
        self.btn_start.clicked.connect(self.start_backup)
        self.btn_stop.clicked.connect(self.stop_backup)

    def add_source(self):
        dlg = QtWidgets.QFileDialog(self, "Select source folder")
        dlg.setFileMode(QtWidgets.QFileDialog.Directory)
        dlg.setOption(QtWidgets.QFileDialog.ShowDirsOnly, True)
        if dlg.exec_() == QtWidgets.QDialog.Accepted:
            for path in dlg.selectedFiles():
                self.src_list.addItem(path)
        self.update_cmd_preview()

    def remove_selected_sources(self):
        for item in self.src_list.selectedItems():
            row = self.src_list.row(item)
            self.src_list.takeItem(row)
        self.update_cmd_preview()

    def choose_destination(self):
        path = QtWidgets.QFileDialog.getExistingDirectory(self, "Select destination folder")
        if path:
            self.dst_edit.setText(path)
        self.update_cmd_preview()

    def build_rsync_args(self) -> List[str]:
        # Base flags
        args: List[str] = []
        if self.chk_archive.isChecked():
            args.append("-a")
        if self.chk_verbose.isChecked():
            args.append("-v")
        if self.chk_compress.isChecked():
            args.append("-z")
        if self.chk_preserve.isChecked():
            # perms/owner/group/devices/times similar to -pogDt (capital D for devices)
            args.extend(["-p", "-o", "-g", "-D", "-t"])
        if self.chk_delete.isChecked():
            args.append("--delete")
        if self.chk_dry.isChecked():
            args.append("--dry-run")
        if self.chk_progress.isChecked():
            args.append("--info=progress2")

        # Excludes
        excludes = [ln.strip() for ln in self.exclude_edit.toPlainText().splitlines() if ln.strip()]
        for pat in excludes:
            args.extend(["--exclude", pat])

        # Sources
        sources = [self.src_list.item(i).text() for i in range(self.src_list.count())]
        # Append trailing slashes for directories to copy contents (rsync convention)
        norm_sources = []
        for s in sources:
            if s and os.path.isdir(s) and not s.endswith("/"):
                s = s + "/"
            norm_sources.append(s)

        # Destination
        dest = self.dst_edit.text().strip()

        # Ensure we have required fields
        if not norm_sources:
            raise ValueError("Please add at least one source folder.")
        if not dest:
            raise ValueError("Please select a destination folder.")

        # Create destination if it doesn't exist (rsync can create, but we informally ensure)
        # We'll not auto-create here; rsync will create if parent exists. Keep it simple.

        # Final args (sources + dest)
        args.extend(norm_sources)
        args.append(dest)
        return args

    def update_cmd_preview(self):
        try:
            args = self.build_rsync_args()
            cmd = "rsync " + " ".join(shlex.quote(a) for a in args)
            self.cmd_preview.setText(cmd)
        except Exception as e:
            self.cmd_preview.setText(f"Error: {e}")

    def start_backup(self):
        try:
            args = self.build_rsync_args()
        except Exception as e:
            QtWidgets.QMessageBox.warning(self, "Invalid configuration", str(e))
            return
        self.log.clear()
        self.progress.setValue(0)
        self._update_buttons(running=True)
        self.runner.start(args)

    def stop_backup(self):
        self.runner.stop()

    def append_log(self, text: str):
        self.log.appendPlainText(text)
        # Autoscroll
        cursor = self.log.textCursor()
        cursor.movePosition(QtGui.QTextCursor.End)
        self.log.setTextCursor(cursor)

    def on_progress(self, pct: int):
        self.progress.setValue(pct)

    def on_started(self):
        self.statusBar().showMessage("Running rsync…")

    def on_state(self, state: str):
        self.statusBar().showMessage(f"State: {state}")

    def on_finished(self, code: int):
        self._update_buttons(running=False)
        if code == 0:
            self.statusBar().showMessage("Backup finished successfully (exit 0)")
        else:
            self.statusBar().showMessage(f"rsync exited with code {code}")
            QtWidgets.QMessageBox.warning(self, "rsync finished with errors", f"Exit code: {code}")

    def _update_buttons(self, *, running: bool):
        self.btn_start.setEnabled(not running)
        self.btn_stop.setEnabled(running)
        self.btn_add_src.setEnabled(not running)
        self.btn_del_src.setEnabled(not running)
        self.btn_browse_dst.setEnabled(not running)


def main():
    app = QtWidgets.QApplication(sys.argv)
    app.setApplicationName("Rsync Backup")
    app.setOrganizationName("Example")

    font = app.font()
    if sys.platform.startswith("linux"):
        font.setPointSize(10)
        app.setFont(font)

    w = MainWindow()
    w.show()
    sys.exit(app.exec_())


if __name__ == "__main__":
    main()
