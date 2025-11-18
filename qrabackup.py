#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Simple Backup App for Linux using PyQt5 and rsync, with profiles
persisted under ~/.config/qrabackup/settings.json (XDG aware).

New in this version
- Load/Save settings automatically on start/exit (and after edits)
- Settings stored in ~/.config/qrabackup/settings.json
- Multiple backup "locations" (profiles): each has Sources, Destination,
  Excludes, and per-job options. You can add/remove/rename/duplicate.
- Run Selected job (as before) or Run All to execute jobs sequentially.

Dependencies
- Python 3.8+
- PyQt5 (pip install PyQt5)
- rsync in PATH

Run
  python3 qrabackup.py
"""
from __future__ import annotations

import json
import os
import re
import shlex
import sys
from dataclasses import dataclass, asdict
from pathlib import Path
from typing import List, Dict, Any

from PyQt5 import QtCore, QtGui, QtWidgets


def config_file_path() -> Path:
    """Return path to settings.json honoring XDG_CONFIG_HOME."""
    xdg = os.environ.get("XDG_CONFIG_HOME")
    if xdg:
        base = Path(xdg)
    else:
        base = Path.home() / ".config"
    cfg_dir = base / "qrabackup"
    cfg_dir.mkdir(parents=True, exist_ok=True)
    return cfg_dir / "settings.json"


@dataclass
class JobOptions:
    archive: bool = True
    verbose: bool = False
    compress: bool = False
    delete: bool = False
    preserve: bool = False  # perms/owner/group/devices/times
    dry_run: bool = False
    progress: bool = True

    @staticmethod
    def from_dict(d: Dict[str, Any]) -> "JobOptions":
        obj = JobOptions()
        for k in asdict(obj).keys():
            if k in d:
                setattr(obj, k, bool(d[k]))
        return obj


@dataclass
class Job:
    name: str
    sources: List[str]
    destination: str
    excludes: List[str]
    options: JobOptions

    @staticmethod
    def from_dict(d: Dict[str, Any]) -> "Job":
        return Job(
            name=d.get("name", "Job"),
            sources=list(d.get("sources", [])),
            destination=str(d.get("destination", "")),
            excludes=list(d.get("excludes", [])),
            options=JobOptions.from_dict(d.get("options", {})),
        )

    def to_dict(self) -> Dict[str, Any]:
        return {
            "name": self.name,
            "sources": self.sources,
            "destination": self.destination,
            "excludes": self.excludes,
            "options": asdict(self.options),
        }


class RsyncRunner(QtCore.QObject):
    started = QtCore.pyqtSignal()
    line = QtCore.pyqtSignal(str)
    error_line = QtCore.pyqtSignal(str)
    finished = QtCore.pyqtSignal(int)  # exit code
    progress = QtCore.pyqtSignal(int)  # 0..100
    state_changed = QtCore.pyqtSignal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.proc = QtCore.QProcess(self)
        env = QtCore.QProcessEnvironment.systemEnvironment()
        self.proc.setProcessEnvironment(env)
        self.proc.setProcessChannelMode(QtCore.QProcess.MergedChannels)

        self.proc.readyReadStandardOutput.connect(self._read_stdout)
        self.proc.readyReadStandardError.connect(self._read_stderr)
        self.proc.started.connect(self.started)
        self.proc.stateChanged.connect(self._on_state)
        self.proc.finished.connect(self._on_finished)

        self._buf = b""
        self._last_percent = -1

    def start(self, args: List[str]):
        self._buf = b""
        self._last_percent = -1
        self.progress.emit(0)
        program_path = QtCore.QStandardPaths.findExecutable("rsync")
        if not program_path:
            self.error_line.emit("Error: rsync not found in PATH.")
            self.finished.emit(127)
            return
        self.proc.start(program_path, args)

    def stop(self):
        if self.proc.state() != QtCore.QProcess.NotRunning:
            self.proc.kill()

    def _read_stdout(self):
        data = self.proc.readAllStandardOutput()
        if not data:
            return
        self._buf += bytes(data)
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
        m = self._re_percent.search(line)
        if m:
            pct = int(m.group(1))
            pct = max(0, min(100, pct))
            if pct != self._last_percent:
                self._last_percent = pct
                self.progress.emit(pct)
            return
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
        self.setWindowTitle("Qrabackup Backup 0.1")
        self.resize(1080, 720)

        self.runner = RsyncRunner(self)
        self.runner.started.connect(self.on_started)
        self.runner.line.connect(self.append_log)
        self.runner.error_line.connect(self.append_log)
        self.runner.progress.connect(self.on_progress)
        self.runner.finished.connect(self.on_single_finished)
        self.runner.state_changed.connect(self.on_state)

        # App state
        self.jobs: List[Job] = []
        self.current_job_index: int = -1
        self.run_queue: List[int] = []  # indices to run sequentially

        self._build_ui()
        self._connect_form_change_signals()
        self.load_settings()
        if not self.jobs:
            self._add_default_job()
        self._select_job(0)
        self._update_buttons(running=False)

    def _build_ui(self):
        # Menu
        menu = self.menuBar()
        file_menu = menu.addMenu("&File")
        act_save = file_menu.addAction("Save Settings")
        act_save.triggered.connect(self.save_settings)
        act_reload = file_menu.addAction("Reload Settings")
        act_reload.triggered.connect(self.reload_settings)
        file_menu.addSeparator()
        act_quit = file_menu.addAction("Quit")
        act_quit.triggered.connect(self.close)

        central = QtWidgets.QWidget()
        self.setCentralWidget(central)
        hsplit = QtWidgets.QHBoxLayout(central)

        # Left: jobs list
        left_box = QtWidgets.QGroupBox("Backup Locations (Profiles)")
        left_layout = QtWidgets.QVBoxLayout(left_box)
        self.jobs_list = QtWidgets.QListWidget()
        self.jobs_list.currentRowChanged.connect(self._on_job_selected)
        btn_row = QtWidgets.QHBoxLayout()
        self.btn_job_add = QtWidgets.QPushButton("Add…")
        self.btn_job_dup = QtWidgets.QPushButton("Duplicate")
        self.btn_job_ren = QtWidgets.QPushButton("Rename…")
        self.btn_job_del = QtWidgets.QPushButton("Remove")
        btn_row.addWidget(self.btn_job_add)
        btn_row.addWidget(self.btn_job_dup)
        btn_row.addWidget(self.btn_job_ren)
        btn_row.addWidget(self.btn_job_del)
        left_layout.addWidget(self.jobs_list)
        left_layout.addLayout(btn_row)

        # Right: job editor + controls
        right = QtWidgets.QVBoxLayout()

        # Sources
        src_group = QtWidgets.QGroupBox("Sources for selected profile")
        src_layout = QtWidgets.QVBoxLayout(src_group)
        self.src_list = QtWidgets.QListWidget()
        self.src_list.setSelectionMode(QtWidgets.QAbstractItemView.ExtendedSelection)
        sbtn_row = QtWidgets.QHBoxLayout()
        self.btn_add_src = QtWidgets.QPushButton("Add folder…")
        self.btn_del_src = QtWidgets.QPushButton("Remove selected")
        sbtn_row.addWidget(self.btn_add_src)
        sbtn_row.addWidget(self.btn_del_src)
        sbtn_row.addStretch(1)
        src_layout.addWidget(self.src_list)
        src_layout.addLayout(sbtn_row)

        # Destination
        dst_group = QtWidgets.QGroupBox("Destination")
        dst_layout = QtWidgets.QHBoxLayout(dst_group)
        self.dst_edit = QtWidgets.QLineEdit()
        self.dst_edit.setPlaceholderText("/path/to/backup/target")
        self.btn_browse_dst = QtWidgets.QPushButton("Browse…")
        dst_layout.addWidget(self.dst_edit, 1)
        dst_layout.addWidget(self.btn_browse_dst)

        # Options
        opt_group = QtWidgets.QGroupBox("Options (per profile)")
        form = QtWidgets.QGridLayout(opt_group)
        self.chk_archive = QtWidgets.QCheckBox("Archive (-a)")
        self.chk_archive.setChecked(True)
        self.chk_verbose = QtWidgets.QCheckBox("Verbose (-v)")
        self.chk_compress = QtWidgets.QCheckBox("Compress (-z)")
        self.chk_delete = QtWidgets.QCheckBox("Delete extras (--delete)")
        self.chk_preserve = QtWidgets.QCheckBox("Preserve perms/owner/group/devices/times (-pgoDt)")
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

        # Excludes
        ex_label = QtWidgets.QLabel("Exclude patterns (one per line):")
        self.exclude_edit = QtWidgets.QPlainTextEdit()
        self.exclude_edit.setPlaceholderText("*.tmp\n.cache/\nnode_modules/\n.DS_Store\nThumbs.db")
        self.exclude_edit.setFixedHeight(90)

        # Command preview
        self.cmd_preview = QtWidgets.QLineEdit()
        self.cmd_preview.setReadOnly(True)
        self.cmd_preview.setPlaceholderText("rsync command will appear here…")

        # Controls
        controls = QtWidgets.QHBoxLayout()
        self.btn_build_cmd = QtWidgets.QPushButton("Preview Command")
        self.btn_start = QtWidgets.QPushButton("Run Selected")
        self.btn_run_all = QtWidgets.QPushButton("Run All")
        self.btn_stop = QtWidgets.QPushButton("Stop")
        self.progress = QtWidgets.QProgressBar()
        self.progress.setRange(0, 100)
        self.progress.setValue(0)
        controls.addWidget(self.btn_build_cmd)
        controls.addStretch(1)
        controls.addWidget(self.progress, 2)
        controls.addWidget(self.btn_start)
        controls.addWidget(self.btn_run_all)
        controls.addWidget(self.btn_stop)

        # Log
        self.log = QtWidgets.QPlainTextEdit()
        self.log.setReadOnly(True)
        self.log.setWordWrapMode(QtGui.QTextOption.NoWrap)

        # Layout assembly
        right.addWidget(src_group)
        right.addWidget(dst_group)
        right.addWidget(opt_group)
        right.addWidget(ex_label)
        right.addWidget(self.exclude_edit)
        right.addLayout(controls)
        right.addWidget(self.cmd_preview)
        right.addWidget(self.log, 10)

        hsplit.addWidget(left_box, 1)
        container = QtWidgets.QWidget()
        container.setLayout(right)
        hsplit.addWidget(container, 3)

        # Button wiring
        self.btn_add_src.clicked.connect(self.add_source)
        self.btn_del_src.clicked.connect(self.remove_selected_sources)
        self.btn_browse_dst.clicked.connect(self.choose_destination)
        self.btn_build_cmd.clicked.connect(self.update_cmd_preview)
        self.btn_start.clicked.connect(self.start_selected)
        self.btn_run_all.clicked.connect(self.start_all)
        self.btn_stop.clicked.connect(self.stop_backup)

        self.btn_job_add.clicked.connect(self.add_job)
        self.btn_job_dup.clicked.connect(self.duplicate_job)
        self.btn_job_ren.clicked.connect(self.rename_job)
        self.btn_job_del.clicked.connect(self.delete_job)

    def _connect_form_change_signals(self):
        # Any change marks dirty and saves
        for chk in [
            self.chk_archive,
            self.chk_verbose,
            self.chk_compress,
            self.chk_delete,
            self.chk_preserve,
            self.chk_dry,
            self.chk_progress,
        ]:
            chk.toggled.connect(self._form_changed)
        self.dst_edit.textChanged.connect(self._form_changed)
        self.exclude_edit.textChanged.connect(self._form_changed)
        self.src_list.model().rowsInserted.connect(lambda *_: self._form_changed())
        self.src_list.model().rowsRemoved.connect(lambda *_: self._form_changed())

    def _add_default_job(self):
        self.jobs.append(Job("Default", [], "", [], JobOptions()))
        self._refresh_jobs_list()

    def _refresh_jobs_list(self):
        self.jobs_list.clear()
        for j in self.jobs:
            self.jobs_list.addItem(j.name)

    def _on_job_selected(self, row: int):
        if row < 0 or row >= len(self.jobs):
            return
        # Save previous form into model first
        if 0 <= self.current_job_index < len(self.jobs):
            self._collect_form_into_job(self.jobs[self.current_job_index])
        self.current_job_index = row
        self._load_job_into_form(self.jobs[row])
        self.update_cmd_preview()

    def _select_job(self, idx: int):
        self.jobs_list.setCurrentRow(idx)

    def add_job(self):
        name, ok = QtWidgets.QInputDialog.getText(self, "Add profile", "Name:", text=f"Job {len(self.jobs)+1}")
        if not ok or not name.strip():
            return
        self.jobs.append(Job(name.strip(), [], "", [], JobOptions()))
        self._refresh_jobs_list()
        self._select_job(len(self.jobs)-1)
        self.save_settings()

    def duplicate_job(self):
        idx = self.jobs_list.currentRow()
        if idx < 0:
            return
        src = self.jobs[idx]
        clone = Job(src.name + " (copy)", list(src.sources), src.destination, list(src.excludes), JobOptions.from_dict(asdict(src.options)))
        self.jobs.insert(idx+1, clone)
        self._refresh_jobs_list()
        self._select_job(idx+1)
        self.save_settings()

    def rename_job(self):
        idx = self.jobs_list.currentRow()
        if idx < 0:
            return
        name, ok = QtWidgets.QInputDialog.getText(self, "Rename profile", "New name:", text=self.jobs[idx].name)
        if not ok or not name.strip():
            return
        self.jobs[idx].name = name.strip()
        self._refresh_jobs_list()
        self._select_job(idx)
        self.save_settings()

    def delete_job(self):
        idx = self.jobs_list.currentRow()
        if idx < 0:
            return
        if len(self.jobs) == 1:
            QtWidgets.QMessageBox.information(self, "Cannot delete", "At least one profile must exist.")
            return
        if QtWidgets.QMessageBox.question(self, "Remove profile", f"Delete '{self.jobs[idx].name}'?") == QtWidgets.QMessageBox.Yes:
            del self.jobs[idx]
            self._refresh_jobs_list()
            self._select_job(min(idx, len(self.jobs)-1))
            self.save_settings()

    def _load_job_into_form(self, job: Job):
        # Sources
        self.src_list.clear()
        for s in job.sources:
            self.src_list.addItem(s)
        # Destination
        self.dst_edit.setText(job.destination)
        # Excludes
        self.exclude_edit.setPlainText("\n".join(job.excludes))
        # Options
        self.chk_archive.setChecked(job.options.archive)
        self.chk_verbose.setChecked(job.options.verbose)
        self.chk_compress.setChecked(job.options.compress)
        self.chk_delete.setChecked(job.options.delete)
        self.chk_preserve.setChecked(job.options.preserve)
        self.chk_dry.setChecked(job.options.dry_run)
        self.chk_progress.setChecked(job.options.progress)

    def _collect_form_into_job(self, job: Job):
        job.sources = [self.src_list.item(i).text() for i in range(self.src_list.count())]
        job.destination = self.dst_edit.text().strip()
        job.excludes = [ln.strip() for ln in self.exclude_edit.toPlainText().splitlines() if ln.strip()]
        job.options.archive = self.chk_archive.isChecked()
        job.options.verbose = self.chk_verbose.isChecked()
        job.options.compress = self.chk_compress.isChecked()
        job.options.delete = self.chk_delete.isChecked()
        job.options.preserve = self.chk_preserve.isChecked()
        job.options.dry_run = self.chk_dry.isChecked()
        job.options.progress = self.chk_progress.isChecked()

    def _form_changed(self):
        if 0 <= self.current_job_index < len(self.jobs):
            self._collect_form_into_job(self.jobs[self.current_job_index])
            self.update_cmd_preview()
            self.save_settings()  # auto-save on edits

    def add_source(self):
        dlg = QtWidgets.QFileDialog(self, "Select source folder")
        dlg.setFileMode(QtWidgets.QFileDialog.Directory)
        dlg.setOption(QtWidgets.QFileDialog.ShowDirsOnly, True)
        if dlg.exec_() == QtWidgets.QDialog.Accepted:
            for path in dlg.selectedFiles():
                self.src_list.addItem(path)
        self._form_changed()

    def remove_selected_sources(self):
        for item in self.src_list.selectedItems():
            self.src_list.takeItem(self.src_list.row(item))
        self._form_changed()

    def choose_destination(self):
        path = QtWidgets.QFileDialog.getExistingDirectory(self, "Select destination folder")
        if path:
            self.dst_edit.setText(path)
        self._form_changed()

    def _rsync_args_for_job(self, job: Job) -> List[str]:
        args: List[str] = []
        if job.options.archive:
            args.append("-a")
        if job.options.verbose:
            args.append("-v")
        if job.options.compress:
            args.append("-z")
        if job.options.preserve:
            args += ["-p", "-o", "-g", "-D", "-t"]
        if job.options.delete:
            args.append("--delete")
        if job.options.dry_run:
            args.append("--dry-run")
        if job.options.progress:
            args.append("--info=progress2")
        for pat in job.excludes:
            args += ["--exclude", pat]
        if not job.sources:
            raise ValueError("Profile has no sources")
        if not job.destination:
            raise ValueError("Profile has no destination")
        # Ensure trailing slash for directories
        norm_sources = []
        for s in job.sources:
            if s and os.path.isdir(s) and not s.endswith("/"):
                s = s + "/"
            norm_sources.append(s)
        args += norm_sources
        args.append(job.destination)
        return args

    def update_cmd_preview(self):
        try:
            if 0 <= self.current_job_index < len(self.jobs):
                args = self._rsync_args_for_job(self.jobs[self.current_job_index])
                self.cmd_preview.setText("rsync " + " ".join(shlex.quote(a) for a in args))
            else:
                self.cmd_preview.setText("")
        except Exception as e:
            self.cmd_preview.setText(f"Error: {e}")

    def start_selected(self):
        if not (0 <= self.current_job_index < len(self.jobs)):
            return
        job = self.jobs[self.current_job_index]
        try:
            args = self._rsync_args_for_job(job)
        except Exception as e:
            QtWidgets.QMessageBox.warning(self, "Invalid configuration", str(e))
            return
        self.log.appendPlainText(f"=== Running: {job.name} ===")
        self.progress.setValue(0)
        self._update_buttons(running=True)
        try:
            self.runner.finished.disconnect()
        except TypeError:
            pass
        self.runner.finished.connect(self.on_single_finished)
        self.runner.start(args)

    def start_all(self):
        if not self.jobs:
            return
        # Save current edits
        if 0 <= self.current_job_index < len(self.jobs):
            self._collect_form_into_job(self.jobs[self.current_job_index])
        # Create queue of indices
        self.run_queue = list(range(len(self.jobs)))
        self.log.appendPlainText("=== Running ALL profiles sequentially ===")
        self._update_buttons(running=True)
        try:
            self.runner.finished.disconnect()
        except TypeError:
            pass
        self.runner.finished.connect(self.on_queue_finished)
        self._run_next_in_queue()

    def _run_next_in_queue(self):
        if not self.run_queue:
            self.on_queue_done()
            return
        idx = self.run_queue.pop(0)
        job = self.jobs[idx]
        try:
            args = self._rsync_args_for_job(job)
        except Exception as e:
            self.log.appendPlainText(f"[SKIP] {job.name}: {e}")
            self._run_next_in_queue()
            return
        self.log.appendPlainText(f"=== Running: {job.name} ===")
        self.progress.setValue(0)
        self.runner.start(args)

    def on_single_finished(self, code: int):
        self._update_buttons(running=False)
        self.statusBar().showMessage(f"rsync exited {code}")
        self.log.appendPlainText(f"=== Finished (exit {code}) ===\n")

    def on_queue_finished(self, code: int):
        self.log.appendPlainText(f"=== Finished job (exit {code}) ===")
        self._run_next_in_queue()

    def on_queue_done(self):
        self._update_buttons(running=False)
        self.statusBar().showMessage("All jobs finished")
        self.log.appendPlainText("=== All profiles finished ===\n")

    def stop_backup(self):
        self.run_queue.clear()
        self.runner.stop()

    def append_log(self, text: str):
        if not text:
            return
        self.log.appendPlainText(text)
        cursor = self.log.textCursor()
        cursor.movePosition(QtGui.QTextCursor.End)
        self.log.setTextCursor(cursor)

    def on_progress(self, pct: int):
        self.progress.setValue(pct)

    def on_started(self):
        self.statusBar().showMessage("Running rsync…")

    def on_state(self, state: str):
        self.statusBar().showMessage(f"State: {state}")

    def _update_buttons(self, *, running: bool):
        self.btn_start.setEnabled(not running if hasattr(self, 'btn_start') else True)
        # Simpler, explicit setting for all buttons
        self.btn_start.setEnabled(not running)
        self.btn_run_all.setEnabled(not running)
        self.btn_stop.setEnabled(running)
        self.btn_add_src.setEnabled(not running)
        self.btn_del_src.setEnabled(not running)
        self.btn_browse_dst.setEnabled(not running)
        self.btn_job_add.setEnabled(not running)
        self.btn_job_dup.setEnabled(not running)
        self.btn_job_ren.setEnabled(not running)
        self.btn_job_del.setEnabled(not running)

    def load_settings(self):
        path = config_file_path()
        if not path.exists():
            return
        try:
            data = json.loads(path.read_text())
        except Exception as e:
            QtWidgets.QMessageBox.warning(self, "Settings load error", str(e))
            return
        jobs = data.get("jobs", [])
        self.jobs = [Job.from_dict(j) for j in jobs]
        self._refresh_jobs_list()
        # window
        w = data.get("window", {})
        size = w.get("size")
        if isinstance(size, list) and len(size) == 2:
            self.resize(int(size[0]), int(size[1]))

    def save_settings(self):
        # Ensure current form is flushed into model
        if 0 <= self.current_job_index < len(self.jobs):
            self._collect_form_into_job(self.jobs[self.current_job_index])
        data = {
            "jobs": [j.to_dict() for j in self.jobs],
            "window": {"size": [self.width(), self.height()]},
        }
        path = config_file_path()
        try:
            tmp = path.with_suffix(".tmp")
            tmp.write_text(json.dumps(data, indent=2))
            tmp.replace(path)
            self.statusBar().showMessage(f"Saved settings → {path}", 3000)
        except Exception as e:
            QtWidgets.QMessageBox.warning(self, "Settings save error", str(e))

    def reload_settings(self):
        self.load_settings()
        if self.jobs:
            self._select_job(0)

    def closeEvent(self, e: QtGui.QCloseEvent):
        try:
            self.save_settings()
        finally:
            super().closeEvent(e)


def main():
    app = QtWidgets.QApplication(sys.argv)
    app.setApplicationName("Rsync Backup")
    app.setOrganizationName("qrabackup")

    font = app.font()
    if sys.platform.startswith("linux"):
        font.setPointSize(10)
        app.setFont(font)

    w = MainWindow()
    w.show()
    sys.exit(app.exec_())


if __name__ == "__main__":
    main()
