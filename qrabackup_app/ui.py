"""Qt UI: SnapshotsDialog, MainWindow, and main().

All command construction, run planning, parsing and settings serialization live
in the pure ``commands``/``parsers``/``settings``/``model`` modules; this file
is the Qt glue (widgets, signals, dialogs, the run loop, and error reporting).
"""
from __future__ import annotations

import json
import os
import sys
import time
import uuid
from datetime import datetime
from typing import Any, Callable, Dict, List, Optional

from PyQt5 import QtCore, QtGui, QtWidgets

from . import __version__, commands, settings
from .commands import RunStep
from .formats import format_last_run, human_bytes
from .model import Job, LastRun, ResticConfig, ResticRetention, new_job
from .parsers import PlainProgressParser, diagnose_restic_failure
from .runner import CaptureRunner, CommandRunner


class SnapshotsDialog(QtWidgets.QDialog):
    """Lists restic snapshots and lets the user pick one to restore or forget.
    The chosen action/snapshot is read by the caller after exec_()."""

    def __init__(self, parent, snapshots: List[Dict[str, Any]]):
        super().__init__(parent)
        self.setWindowTitle("restic snapshots")
        self.resize(840, 440)
        self.snapshots = snapshots
        self.chosen_action: Optional[str] = None   # "restore" | "forget"
        self.chosen_snapshot: Optional[Dict[str, Any]] = None

        v = QtWidgets.QVBoxLayout(self)
        self.table = QtWidgets.QTableWidget(len(snapshots), 5)
        self.table.setHorizontalHeaderLabels(["ID", "Time", "Host", "Tags", "Paths"])
        self.table.setSelectionBehavior(QtWidgets.QAbstractItemView.SelectRows)
        self.table.setSelectionMode(QtWidgets.QAbstractItemView.SingleSelection)
        self.table.setEditTriggers(QtWidgets.QAbstractItemView.NoEditTriggers)
        self.table.verticalHeader().setVisible(False)
        for row, s in enumerate(snapshots):
            sid = s.get("short_id") or str(s.get("id", ""))[:8]
            t = str(s.get("time", ""))[:19].replace("T", " ")
            host = s.get("hostname", "")
            tags = ", ".join(s.get("tags") or [])
            paths = ", ".join(s.get("paths") or [])
            for col, val in enumerate([sid, t, host, tags, paths]):
                self.table.setItem(row, col, QtWidgets.QTableWidgetItem(val))
        self.table.resizeColumnsToContents()
        self.table.horizontalHeader().setStretchLastSection(True)
        if snapshots:
            self.table.selectRow(0)
        v.addWidget(self.table)

        btns = QtWidgets.QHBoxLayout()
        self.btn_restore = QtWidgets.QPushButton("Restore selected…")
        self.btn_forget = QtWidgets.QPushButton("Forget selected")
        btn_close = QtWidgets.QPushButton("Close")
        btns.addWidget(self.btn_restore)
        btns.addWidget(self.btn_forget)
        btns.addStretch(1)
        btns.addWidget(btn_close)
        v.addLayout(btns)

        self.btn_restore.clicked.connect(lambda: self._choose("restore"))
        self.btn_forget.clicked.connect(lambda: self._choose("forget"))
        self.table.doubleClicked.connect(lambda *_: self._choose("restore"))
        btn_close.clicked.connect(self.reject)

    def _selected(self) -> Optional[Dict[str, Any]]:
        row = self.table.currentRow()
        if 0 <= row < len(self.snapshots):
            return self.snapshots[row]
        return None

    def _choose(self, action: str):
        s = self._selected()
        if not s:
            QtWidgets.QMessageBox.information(self, "No selection", "Select a snapshot first.")
            return
        self.chosen_action = action
        self.chosen_snapshot = s
        self.accept()


class MainWindow(QtWidgets.QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle(f"Qrabackup Backup {__version__}")
        self.resize(1080, 760)

        self.runner = CommandRunner(self)
        self.runner.started.connect(self.on_started)
        self.runner.line.connect(self.append_log)
        self.runner.error_line.connect(self.append_log)
        self.runner.progress.connect(self.on_progress)
        self.runner.progress_line.connect(self.on_progress_line)
        self.runner.finished.connect(self.on_step_finished)
        self.runner.state_changed.connect(self.on_state)

        # Non-blocking capture for restic metadata reads (snapshots, stats,
        # forget --dry-run, cat config). One at a time; Stop cancels it.
        self.capture = CaptureRunner(self)
        self.capture.done.connect(self._on_capture_done)
        self._capture_cb: Optional[Callable[[int, str, str], None]] = None
        self._capture_cancelled = False

        # App state
        self.jobs: List[Job] = []
        self.current_job_index: int = -1
        # Sequential run queue. Each "run" (a single profile, or all profiles)
        # is expanded into steps; a restic profile with retention enabled adds a
        # forget+prune step after its backup.
        self._steps: List[RunStep] = []
        self._current_step: Optional[RunStep] = None
        self._step_started_at: float = 0.0  # time.monotonic() at step start

        # True while a profile is being loaded into the form, so the form-change
        # handlers don't write the half-populated form back into the model.
        self._loading: bool = False
        # Coalesce rapid edits (e.g. typing) into a single debounced save.
        self._save_timer = QtCore.QTimer(self)
        self._save_timer.setSingleShot(True)
        self._save_timer.setInterval(800)
        self._save_timer.timeout.connect(self.save_settings)

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

        # Engine selector
        engine_row = QtWidgets.QHBoxLayout()
        engine_row.addWidget(QtWidgets.QLabel("Engine:"))
        self.rb_rsync = QtWidgets.QRadioButton("rsync (local/mounted)")
        self.rb_restic = QtWidgets.QRadioButton("restic (S3, encrypted)")
        self.rb_rsync.setChecked(True)
        self.engine_group = QtWidgets.QButtonGroup(self)
        self.engine_group.addButton(self.rb_rsync)
        self.engine_group.addButton(self.rb_restic)
        engine_row.addWidget(self.rb_rsync)
        engine_row.addWidget(self.rb_restic)
        engine_row.addStretch(1)

        # Sources (shared)
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

        # Excludes (shared)
        ex_label = QtWidgets.QLabel("Exclude patterns (one per line):")
        self.exclude_edit = QtWidgets.QPlainTextEdit()
        self.exclude_edit.setPlaceholderText("*.tmp\n.cache/\nnode_modules/\n.DS_Store\nThumbs.db")
        self.exclude_edit.setFixedHeight(80)

        # Engine-specific editor stack
        self.engine_stack = QtWidgets.QStackedWidget()
        self.engine_stack.addWidget(self._build_rsync_page())   # index 0
        self.engine_stack.addWidget(self._build_restic_page())  # index 1

        # Command preview (shared)
        self.cmd_preview = QtWidgets.QLineEdit()
        self.cmd_preview.setReadOnly(True)
        self.cmd_preview.setPlaceholderText("command will appear here…")

        # Controls (shared)
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

        # Log (shared)
        self.log = QtWidgets.QPlainTextEdit()
        self.log.setReadOnly(True)
        self.log.setWordWrapMode(QtGui.QTextOption.NoWrap)

        # Assembly
        right.addLayout(engine_row)
        right.addWidget(src_group)
        right.addWidget(ex_label)
        right.addWidget(self.exclude_edit)
        right.addWidget(self.engine_stack)
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
        self.rb_restic.toggled.connect(self._on_engine_changed)

        self.btn_job_add.clicked.connect(self.add_job)
        self.btn_job_dup.clicked.connect(self.duplicate_job)
        self.btn_job_ren.clicked.connect(self.rename_job)
        self.btn_job_del.clicked.connect(self.delete_job)

        # restic action buttons
        self.btn_res_test.clicked.connect(self.restic_test_connection)
        self.btn_res_init.clicked.connect(self.restic_init)
        self.btn_res_snap.clicked.connect(self.restic_snapshots)
        self.btn_res_check.clicked.connect(self.restic_check)
        self.btn_res_unlock.clicked.connect(self.restic_unlock)
        self.btn_res_prune.clicked.connect(self.restic_forget_prune)
        self.btn_res_stats.clicked.connect(self.restic_stats)
        self.btn_res_export.clicked.connect(self.export_credentials)

    def _build_rsync_page(self) -> QtWidgets.QWidget:
        page = QtWidgets.QWidget()
        v = QtWidgets.QVBoxLayout(page)
        v.setContentsMargins(0, 0, 0, 0)

        dst_group = QtWidgets.QGroupBox("Destination")
        dst_layout = QtWidgets.QHBoxLayout(dst_group)
        self.dst_edit = QtWidgets.QLineEdit()
        self.dst_edit.setPlaceholderText("/path/to/backup/target")
        self.btn_browse_dst = QtWidgets.QPushButton("Browse…")
        dst_layout.addWidget(self.dst_edit, 1)
        dst_layout.addWidget(self.btn_browse_dst)

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

        v.addWidget(dst_group)
        v.addWidget(opt_group)
        v.addStretch(1)
        return page

    def _build_restic_page(self) -> QtWidgets.QWidget:
        page = QtWidgets.QWidget()
        v = QtWidgets.QVBoxLayout(page)
        v.setContentsMargins(0, 0, 0, 0)

        # Repository
        repo_group = QtWidgets.QGroupBox("S3 Repository")
        rg = QtWidgets.QGridLayout(repo_group)
        self.res_bucket = QtWidgets.QLineEdit()
        self.res_bucket.setPlaceholderText("my-bucket")
        self.res_region = QtWidgets.QLineEdit()
        self.res_region.setPlaceholderText("eu-central-1 (blank for default)")
        self.res_endpoint = QtWidgets.QLineEdit()
        self.res_endpoint.setPlaceholderText("blank = AWS; e.g. https://minio.local:9000")
        self.res_prefix = QtWidgets.QLineEdit()
        self.res_prefix.setPlaceholderText("optional path within bucket, e.g. laptop")
        self.res_repo_override = QtWidgets.QLineEdit()
        self.res_repo_override.setPlaceholderText("advanced: raw repository, e.g. s3:… (overrides fields above)")
        self.res_repo_preview = QtWidgets.QLineEdit()
        self.res_repo_preview.setReadOnly(True)
        self.res_repo_preview.setPlaceholderText("computed repository URL")
        rg.addWidget(QtWidgets.QLabel("Bucket:"), 0, 0)
        rg.addWidget(self.res_bucket, 0, 1)
        rg.addWidget(QtWidgets.QLabel("Region:"), 0, 2)
        rg.addWidget(self.res_region, 0, 3)
        rg.addWidget(QtWidgets.QLabel("Endpoint:"), 1, 0)
        rg.addWidget(self.res_endpoint, 1, 1)
        rg.addWidget(QtWidgets.QLabel("Prefix:"), 1, 2)
        rg.addWidget(self.res_prefix, 1, 3)
        rg.addWidget(QtWidgets.QLabel("Raw repo:"), 2, 0)
        rg.addWidget(self.res_repo_override, 2, 1, 1, 3)
        rg.addWidget(QtWidgets.QLabel("Repository:"), 3, 0)
        rg.addWidget(self.res_repo_preview, 3, 1, 1, 3)

        # Credentials
        cred_group = QtWidgets.QGroupBox("Credentials (stored in settings.json, masked here)")
        cg = QtWidgets.QGridLayout(cred_group)
        self.res_password = QtWidgets.QLineEdit()
        self.res_password.setEchoMode(QtWidgets.QLineEdit.Password)
        self.res_password.setPlaceholderText("restic repository password (encryption key — keep it safe!)")
        self.res_akid = QtWidgets.QLineEdit()
        self.res_akid.setPlaceholderText("AWS_ACCESS_KEY_ID")
        self.res_secret = QtWidgets.QLineEdit()
        self.res_secret.setEchoMode(QtWidgets.QLineEdit.Password)
        self.res_secret.setPlaceholderText("AWS_SECRET_ACCESS_KEY")
        self.chk_show_secrets = QtWidgets.QCheckBox("Show")
        self.chk_show_secrets.toggled.connect(self._toggle_secret_echo)
        self.btn_res_export = QtWidgets.QPushButton("Export…")
        self.btn_res_export.setToolTip(
            "Save this profile's repository URL, password and S3 keys to a "
            "plaintext file for offline safekeeping.")
        cg.addWidget(QtWidgets.QLabel("Repo password:"), 0, 0)
        cg.addWidget(self.res_password, 0, 1)
        cg.addWidget(self.chk_show_secrets, 0, 2)
        cg.addWidget(self.btn_res_export, 0, 3)
        cg.addWidget(QtWidgets.QLabel("S3 access key:"), 1, 0)
        cg.addWidget(self.res_akid, 1, 1, 1, 3)
        cg.addWidget(QtWidgets.QLabel("S3 secret key:"), 2, 0)
        cg.addWidget(self.res_secret, 2, 1, 1, 3)

        # Retention: when enabled, a forget --prune step runs after each
        # successful backup; the "Forget + Prune…" button applies it on demand.
        ret_group = QtWidgets.QGroupBox("Retention (forget --prune after backup when enabled)")
        rl = QtWidgets.QGridLayout(ret_group)
        self.ret_enabled = QtWidgets.QCheckBox("Run forget --prune after a successful backup")
        self.ret_prune = QtWidgets.QCheckBox("prune")
        self.ret_prune.setChecked(True)
        self.ret_last = self._spin()
        self.ret_daily = self._spin(7)
        self.ret_weekly = self._spin(4)
        self.ret_monthly = self._spin(6)
        self.ret_yearly = self._spin(0)
        rl.addWidget(self.ret_enabled, 0, 0, 1, 6)
        for col, (lbl, w) in enumerate([
            ("last", self.ret_last), ("daily", self.ret_daily),
            ("weekly", self.ret_weekly), ("monthly", self.ret_monthly),
            ("yearly", self.ret_yearly),
        ]):
            rl.addWidget(QtWidgets.QLabel(f"keep {lbl}"), 1, col)
            rl.addWidget(w, 2, col)
        rl.addWidget(self.ret_prune, 2, 5)

        # Tags + actions
        tag_row = QtWidgets.QHBoxLayout()
        tag_row.addWidget(QtWidgets.QLabel("Tags (comma-separated):"))
        self.res_tags = QtWidgets.QLineEdit()
        self.res_tags.setPlaceholderText("laptop, docs")
        tag_row.addWidget(self.res_tags, 1)

        act_row = QtWidgets.QHBoxLayout()
        self.btn_res_test = QtWidgets.QPushButton("Test")
        self.btn_res_test.setToolTip(
            "Verify the repository is reachable and the password/credentials "
            "work (runs 'restic cat config').")
        self.btn_res_init = QtWidgets.QPushButton("Init repo")
        self.btn_res_snap = QtWidgets.QPushButton("Snapshots…")
        self.btn_res_check = QtWidgets.QPushButton("Check")
        self.btn_res_unlock = QtWidgets.QPushButton("Unlock")
        self.btn_res_unlock.setToolTip(
            "Remove stale repository locks left by interrupted runs (runs "
            "'restic unlock'; safe if no other restic process is running).")
        self.btn_res_prune = QtWidgets.QPushButton("Forget + Prune…")
        self.btn_res_stats = QtWidgets.QPushButton("Stats")
        for b in (self.btn_res_test, self.btn_res_init, self.btn_res_snap,
                  self.btn_res_check, self.btn_res_unlock, self.btn_res_prune,
                  self.btn_res_stats):
            act_row.addWidget(b)
        act_row.addStretch(1)

        v.addWidget(repo_group)
        v.addWidget(cred_group)
        v.addWidget(ret_group)
        v.addLayout(tag_row)
        v.addLayout(act_row)
        v.addStretch(1)
        return page

    @staticmethod
    def _spin(value: int = 0) -> QtWidgets.QSpinBox:
        sb = QtWidgets.QSpinBox()
        sb.setRange(0, 3650)
        sb.setValue(value)
        sb.setFixedWidth(70)
        return sb

    def _toggle_secret_echo(self, show: bool):
        mode = QtWidgets.QLineEdit.Normal if show else QtWidgets.QLineEdit.Password
        self.res_password.setEchoMode(mode)
        self.res_secret.setEchoMode(mode)

    def _connect_form_change_signals(self):
        for chk in [
            self.chk_archive, self.chk_verbose, self.chk_compress,
            self.chk_delete, self.chk_preserve, self.chk_dry, self.chk_progress,
            self.ret_enabled, self.ret_prune,
        ]:
            chk.toggled.connect(self._form_changed)
        self.dst_edit.textChanged.connect(self._form_changed)
        self.exclude_edit.textChanged.connect(self._form_changed)
        self.src_list.model().rowsInserted.connect(lambda *_: self._form_changed())
        self.src_list.model().rowsRemoved.connect(lambda *_: self._form_changed())

        # restic fields that affect the repository preview
        for le in [self.res_bucket, self.res_region, self.res_endpoint,
                   self.res_prefix, self.res_repo_override]:
            le.textChanged.connect(self._on_repo_field_changed)
        for le in [self.res_password, self.res_akid, self.res_secret, self.res_tags]:
            le.textChanged.connect(self._form_changed)
        for sb in [self.ret_last, self.ret_daily, self.ret_weekly,
                   self.ret_monthly, self.ret_yearly]:
            sb.valueChanged.connect(self._form_changed)

    def _add_default_job(self):
        self.jobs.append(new_job("Default"))
        self._refresh_jobs_list()

    @staticmethod
    def _job_list_label(j: Job) -> str:
        tag = "restic" if j.engine == "restic" else "rsync"
        status = format_last_run(j.last_run)
        return f"{j.name}  [{tag}]" + (f"  {status}" if status else "")

    def _refresh_jobs_list(self):
        self.jobs_list.clear()
        for j in self.jobs:
            self.jobs_list.addItem(self._job_list_label(j))

    def _update_job_list_item(self, idx: int):
        """Update one row's text in place — unlike _refresh_jobs_list this does
        not clear the list, so it is safe mid-run (no selection churn, no
        form collect/reload cycle)."""
        item = self.jobs_list.item(idx)
        if item is not None and 0 <= idx < len(self.jobs):
            item.setText(self._job_list_label(self.jobs[idx]))

    def _on_job_selected(self, row: int):
        if row < 0 or row >= len(self.jobs):
            return
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
        self.jobs.append(new_job(name.strip()))
        self._refresh_jobs_list()
        self._select_job(len(self.jobs) - 1)
        self.save_settings()

    def duplicate_job(self):
        idx = self.jobs_list.currentRow()
        if idx < 0:
            return
        src = self.jobs[idx]
        clone = Job.from_dict(src.to_dict())
        clone.id = uuid.uuid4().hex
        clone.name = src.name + " (copy)"
        clone.last_run = None  # the copy has never run
        self.jobs.insert(idx + 1, clone)
        self._refresh_jobs_list()
        self._select_job(idx + 1)
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
            # Drop the selection before re-selecting: the form still shows the
            # deleted profile, and _on_job_selected would otherwise collect it
            # into whichever job now occupies current_job_index.
            self.current_job_index = -1
            self._refresh_jobs_list()
            self._select_job(min(idx, len(self.jobs) - 1))
            self.save_settings()

    def _load_job_into_form(self, job: Job):
        self._loading = True
        try:
            # Engine
            self.rb_restic.setChecked(job.engine == "restic")
            self.rb_rsync.setChecked(job.engine != "restic")
            self.engine_stack.setCurrentIndex(1 if job.engine == "restic" else 0)

            # Shared
            self.src_list.clear()
            for s in job.sources:
                self.src_list.addItem(s)
            self.exclude_edit.setPlainText("\n".join(job.excludes))

            # rsync
            self.dst_edit.setText(job.destination)
            self.chk_archive.setChecked(job.rsync.archive)
            self.chk_verbose.setChecked(job.rsync.verbose)
            self.chk_compress.setChecked(job.rsync.compress)
            self.chk_delete.setChecked(job.rsync.delete)
            self.chk_preserve.setChecked(job.rsync.preserve)
            self.chk_dry.setChecked(job.rsync.dry_run)
            self.chk_progress.setChecked(job.rsync.progress)

            # restic
            r = job.restic
            self.res_bucket.setText(r.bucket)
            self.res_region.setText(r.region)
            self.res_endpoint.setText(r.endpoint)
            self.res_prefix.setText(r.prefix)
            self.res_repo_override.setText(r.repository)
            self.res_password.setText(r.password)
            self.res_akid.setText(r.access_key_id)
            self.res_secret.setText(r.secret_access_key)
            self.res_tags.setText(", ".join(r.tags))
            self.ret_enabled.setChecked(r.retention.enabled)
            self.ret_prune.setChecked(r.retention.prune)
            self.ret_last.setValue(r.retention.keep_last)
            self.ret_daily.setValue(r.retention.keep_daily)
            self.ret_weekly.setValue(r.retention.keep_weekly)
            self.ret_monthly.setValue(r.retention.keep_monthly)
            self.ret_yearly.setValue(r.retention.keep_yearly)
            self._update_repo_preview()
        finally:
            self._loading = False

    def _collect_restic_from_form(self) -> ResticConfig:
        r = ResticConfig()
        r.bucket = self.res_bucket.text().strip()
        r.region = self.res_region.text().strip()
        r.endpoint = self.res_endpoint.text().strip()
        r.prefix = self.res_prefix.text().strip()
        r.repository = self.res_repo_override.text().strip()
        r.password = self.res_password.text()
        r.access_key_id = self.res_akid.text().strip()
        r.secret_access_key = self.res_secret.text()
        r.tags = [t.strip() for t in self.res_tags.text().split(",") if t.strip()]
        r.retention = ResticRetention(
            enabled=self.ret_enabled.isChecked(),
            keep_last=self.ret_last.value(),
            keep_daily=self.ret_daily.value(),
            keep_weekly=self.ret_weekly.value(),
            keep_monthly=self.ret_monthly.value(),
            keep_yearly=self.ret_yearly.value(),
            prune=self.ret_prune.isChecked(),
        )
        return r

    def _collect_form_into_job(self, job: Job):
        job.engine = "restic" if self.rb_restic.isChecked() else "rsync"
        job.sources = [self.src_list.item(i).text() for i in range(self.src_list.count())]
        job.excludes = [ln.strip() for ln in self.exclude_edit.toPlainText().splitlines() if ln.strip()]
        job.destination = self.dst_edit.text().strip()
        job.rsync.archive = self.chk_archive.isChecked()
        job.rsync.verbose = self.chk_verbose.isChecked()
        job.rsync.compress = self.chk_compress.isChecked()
        job.rsync.delete = self.chk_delete.isChecked()
        job.rsync.preserve = self.chk_preserve.isChecked()
        job.rsync.dry_run = self.chk_dry.isChecked()
        job.rsync.progress = self.chk_progress.isChecked()
        job.restic = self._collect_restic_from_form()

    def _on_engine_changed(self, *_):
        restic = self.rb_restic.isChecked()
        self.engine_stack.setCurrentIndex(1 if restic else 0)
        if not self._loading:
            self._form_changed()
            self._refresh_jobs_list()
            self._select_job(self.current_job_index)

    def _on_repo_field_changed(self, *_):
        if not self._loading:
            self._update_repo_preview()
        self._form_changed()

    def _update_repo_preview(self):
        repo = self._collect_restic_from_form().build_repository()
        self.res_repo_preview.setText(repo)

    def _form_changed(self):
        if self._loading:
            return
        if 0 <= self.current_job_index < len(self.jobs):
            self._collect_form_into_job(self.jobs[self.current_job_index])
            self.update_cmd_preview()
            self._save_timer.start()  # debounced auto-save

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

    def update_cmd_preview(self):
        if not (0 <= self.current_job_index < len(self.jobs)):
            self.cmd_preview.setText("")
            return
        try:
            self.cmd_preview.setText(commands.preview_command(self.jobs[self.current_job_index]))
        except Exception as e:
            self.cmd_preview.setText(f"Error: {e}")

    def _confirm_destructive(self, jobs: List[Job]) -> bool:
        """Warn before running rsync profiles that mirror with --delete (and
        aren't a dry run), since those permanently remove files."""
        risky = [j for j in jobs if j.engine == "rsync" and j.rsync.delete and not j.rsync.dry_run]
        if not risky:
            return True
        names = "\n".join(f"  • {j.name} → {j.destination or '(no destination)'}" for j in risky)
        resp = QtWidgets.QMessageBox.warning(
            self,
            "Confirm mirror with --delete",
            "These profiles use --delete and will permanently remove files in "
            "the destination that no longer exist in the sources:\n\n"
            f"{names}\n\nContinue?",
            QtWidgets.QMessageBox.Yes | QtWidgets.QMessageBox.No,
            QtWidgets.QMessageBox.No,
        )
        return resp == QtWidgets.QMessageBox.Yes

    def start_selected(self):
        if not (0 <= self.current_job_index < len(self.jobs)):
            return
        job = self.jobs[self.current_job_index]
        try:
            steps = commands.build_run_steps(job, group=0)
        except Exception as e:
            QtWidgets.QMessageBox.warning(self, "Invalid configuration", str(e))
            return
        if not self._confirm_destructive([job]):
            return
        self.log.appendPlainText(f"=== Running profile: {job.name} [{job.engine}] ===")
        self._run_steps(steps)

    def start_all(self):
        if not self.jobs:
            return
        if 0 <= self.current_job_index < len(self.jobs):
            self._collect_form_into_job(self.jobs[self.current_job_index])
        if not self._confirm_destructive(self.jobs):
            return
        all_steps: List[RunStep] = []
        for group, job in enumerate(self.jobs):
            try:
                all_steps += commands.build_run_steps(job, group=group)
            except Exception as e:
                self.log.appendPlainText(f"[SKIP] {job.name}: {e}")
        if not all_steps:
            self.log.appendPlainText("Nothing to run.")
            return
        self.log.appendPlainText("=== Running ALL profiles sequentially ===")
        self._run_steps(all_steps)

    def _run_steps(self, steps: List[RunStep]):
        if not steps:
            return
        self._steps = list(steps)
        self._update_buttons(running=True)
        self._reconnect_finished(self.on_step_finished)
        self._run_next_step()

    def _run_next_step(self):
        if not self._steps:
            self._current_step = None
            self._update_buttons(running=False)
            self.statusBar().showMessage("Finished")
            self.log.appendPlainText("=== Finished ===\n")
            return
        step = self._steps.pop(0)
        self._current_step = step
        self._step_started_at = time.monotonic()
        self.log.appendPlainText(f"--- {step.label} ---")
        self.progress.setValue(0)
        self.runner.start(step.program, step.args, step.env, step.parser)

    def on_step_finished(self, code: int):
        step = self._current_step
        self.log.appendPlainText(f"--- exit {code} ---")
        if step is not None and step.records_status and step.job_id:
            self._record_last_run(step.job_id, code)
        skipped = commands.steps_to_skip_on_failure(step, self._steps, code)
        if skipped:
            self.log.appendPlainText(
                f"[SKIP] {len(skipped)} follow-up step(s) for this profile (previous step failed).")
            self._steps = [s for s in self._steps if s.group != step.group]
        self._run_next_step()

    def _record_last_run(self, job_id: str, code: int):
        """Persist the backup step's outcome as the profile's last-run status
        and refresh its list row (in place — a full refresh mid-run would churn
        the selection and stomp the form)."""
        for idx, job in enumerate(self.jobs):
            if job.id == job_id:
                dur = max(0.0, time.monotonic() - self._step_started_at)
                job.last_run = LastRun(
                    when=datetime.now().isoformat(timespec="seconds"),
                    exit_code=code,
                    duration_s=round(dur, 1),
                )
                self._update_job_list_item(idx)
                self._save_timer.start()
                return

    def _reconnect_finished(self, slot):
        try:
            self.runner.finished.disconnect()
        except TypeError:
            pass
        self.runner.finished.connect(slot)

    def stop_backup(self):
        self._steps.clear()
        if self.capture.running():
            self._capture_cancelled = True
            self.capture.cancel()
        self.runner.stop()

    def _current_restic_job(self) -> Optional[Job]:
        if not (0 <= self.current_job_index < len(self.jobs)):
            return None
        job = self.jobs[self.current_job_index]
        if job.engine != "restic":
            QtWidgets.QMessageBox.information(
                self, "Not a restic profile",
                "Switch this profile's engine to restic to use this action.")
            return None
        return job

    def _restic_capture_async(self, label: str, args: List[str],
                              env: Dict[str, str],
                              on_done: Callable[[int, str, str], None],
                              timeout_ms: int = 120000):
        """Run a restic metadata command (snapshots, stats, forget --dry-run,
        cat config) without blocking the UI, then hand (rc, stdout, stderr) to
        ``on_done``. Buttons are disabled while it runs; Stop cancels it."""
        if self.capture.running() or self.runner.proc.state() != QtCore.QProcess.NotRunning:
            return
        self._capture_cb = on_done
        self._capture_cancelled = False
        self._update_buttons(running=True)
        self.statusBar().showMessage(f"{label}…")
        self.capture.start("restic", args, env, timeout_ms)

    def _on_capture_done(self, rc: int, out: str, err: str):
        cb, self._capture_cb = self._capture_cb, None
        self._update_buttons(running=False)
        if self._capture_cancelled:
            self.statusBar().showMessage("Cancelled", 3000)
            return
        self.statusBar().clearMessage()
        if cb:
            cb(rc, out, err)

    def _show_restic_error(self, title: str, rc: int, out: str, err: str):
        msg = (err or out or "").strip() or f"exit code {rc}"
        body = f"restic failed (exit {rc}):\n\n{msg[:2000]}"
        hint = diagnose_restic_failure(f"{err}\n{out}")
        if hint:
            body += f"\n\n{hint}"
        QtWidgets.QMessageBox.warning(self, title, body)

    def restic_init(self):
        job = self._current_restic_job()
        if not job:
            return
        try:
            base, env = commands.restic_base_args(job)
        except ValueError as e:
            QtWidgets.QMessageBox.warning(self, "Invalid configuration", str(e))
            return
        repo = job.restic.build_repository()
        resp = QtWidgets.QMessageBox.question(
            self, "Initialize repository",
            "Create a new encrypted restic repository at:\n\n"
            f"{repo}\n\n"
            "The bucket must already exist and the credentials must be valid. "
            "Keep the repository password safe — without it the backup cannot "
            "be decrypted.\n\nProceed?",
            QtWidgets.QMessageBox.Yes | QtWidgets.QMessageBox.No,
            QtWidgets.QMessageBox.No,
        )
        if resp != QtWidgets.QMessageBox.Yes:
            return
        self._run_steps([RunStep(0, f"restic init → {repo}", "restic",
                                 base + ["init"], env, PlainProgressParser())])

    def restic_check(self):
        job = self._current_restic_job()
        if not job:
            return
        try:
            base, env = commands.restic_base_args(job)
        except ValueError as e:
            QtWidgets.QMessageBox.warning(self, "Invalid configuration", str(e))
            return
        self._run_steps([RunStep(0, "restic check", "restic",
                                 base + ["check"], env, PlainProgressParser())])

    def restic_test_connection(self):
        """Cheap end-to-end credential/repo check: 'cat config' touches the
        backend and needs the repository password, so success proves endpoint,
        bucket, S3 keys and password all work."""
        job = self._current_restic_job()
        if not job:
            return
        try:
            base, env = commands.restic_base_args(job)
        except ValueError as e:
            QtWidgets.QMessageBox.warning(self, "Invalid configuration", str(e))
            return
        self._restic_capture_async(
            "Testing connection", base + ["cat", "config"], env,
            lambda rc, out, err: self._on_test_connection_done(rc, out, err),
            timeout_ms=60000)

    def _on_test_connection_done(self, rc: int, out: str, err: str):
        if rc != 0:
            self._show_restic_error("Test connection", rc, out, err)
            return
        repo_id = ""
        try:
            repo_id = str(json.loads(out or "{}").get("id", ""))[:8]
        except (ValueError, json.JSONDecodeError):
            pass
        QtWidgets.QMessageBox.information(
            self, "Test connection",
            "Repository reachable, credentials and password accepted."
            + (f"\nRepository ID: {repo_id}" if repo_id else ""))

    def restic_unlock(self):
        job = self._current_restic_job()
        if not job:
            return
        try:
            base, env = commands.restic_base_args(job)
        except ValueError as e:
            QtWidgets.QMessageBox.warning(self, "Invalid configuration", str(e))
            return
        # 'restic unlock' only removes stale locks, so no confirmation needed.
        self._run_steps([RunStep(0, f"{job.name}: restic unlock (remove stale locks)",
                                 "restic", base + ["unlock"], env, PlainProgressParser())])

    def export_credentials(self):
        job = self._current_restic_job()
        if not job:
            return
        try:
            text = commands.export_credentials_text(job)
        except ValueError as e:
            QtWidgets.QMessageBox.warning(self, "Export credentials", str(e))
            return
        resp = QtWidgets.QMessageBox.warning(
            self, "Export credentials",
            "This saves the repository URL, password and S3 keys to a PLAINTEXT "
            "file (owner-only permissions). Keep it offline and private — it is "
            "your recovery escrow if this machine or settings.json is lost.\n\n"
            "Continue?",
            QtWidgets.QMessageBox.Yes | QtWidgets.QMessageBox.No,
            QtWidgets.QMessageBox.No,
        )
        if resp != QtWidgets.QMessageBox.Yes:
            return
        safe_name = "".join(c if (c.isalnum() or c in "._-") else "_" for c in job.name)
        suggested = os.path.join(os.path.expanduser("~"),
                                 f"qrabackup-{safe_name}-credentials.txt")
        path, _ = QtWidgets.QFileDialog.getSaveFileName(
            self, "Export restic credentials", suggested, "Text files (*.txt);;All files (*)")
        if not path:
            return
        try:
            fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
            with os.fdopen(fd, "w") as f:
                f.write(text)
        except OSError as e:
            QtWidgets.QMessageBox.warning(self, "Export failed", str(e))
            return
        self.statusBar().showMessage(f"Credentials exported → {path}", 5000)

    def restic_snapshots(self):
        job = self._current_restic_job()
        if not job:
            return
        try:
            base, env = commands.restic_base_args(job)
        except ValueError as e:
            QtWidgets.QMessageBox.warning(self, "Invalid configuration", str(e))
            return
        self._restic_capture_async(
            "Listing snapshots", base + ["snapshots", "--json"], env,
            lambda rc, out, err: self._on_snapshots_listed(job, rc, out, err))

    def _on_snapshots_listed(self, job: Job, rc: int, out: str, err: str):
        if rc != 0:
            self._show_restic_error("List snapshots", rc, out, err)
            return
        try:
            snaps = json.loads(out or "[]")
        except (ValueError, json.JSONDecodeError):
            snaps = []
        if not snaps:
            QtWidgets.QMessageBox.information(
                self, "Snapshots", "No snapshots in this repository yet.")
            return
        dlg = SnapshotsDialog(self, snaps)
        if dlg.exec_() != QtWidgets.QDialog.Accepted or not dlg.chosen_snapshot:
            return
        if dlg.chosen_action == "restore":
            self._restic_restore(job, dlg.chosen_snapshot)
        elif dlg.chosen_action == "forget":
            self._restic_forget_one(job, dlg.chosen_snapshot)

    def _restic_restore(self, job: Job, snap: Dict[str, Any]):
        snap_id = snap.get("short_id") or str(snap.get("id", ""))[:8]
        target = QtWidgets.QFileDialog.getExistingDirectory(self, "Restore into folder")
        if not target:
            return
        try:
            non_empty = any(True for _ in os.scandir(target))
        except OSError:
            non_empty = False
        if non_empty:
            resp = QtWidgets.QMessageBox.warning(
                self, "Target not empty",
                f"'{target}' is not empty. Restoring may overwrite existing "
                "files with the snapshot's contents.\n\nContinue?",
                QtWidgets.QMessageBox.Yes | QtWidgets.QMessageBox.No,
                QtWidgets.QMessageBox.No,
            )
            if resp != QtWidgets.QMessageBox.Yes:
                return
        base, env = commands.restic_base_args(job)
        args = base + ["restore", snap_id, "--target", target]
        self._run_steps([RunStep(0, f"restic restore {snap_id} → {target}", "restic",
                                 args, env, PlainProgressParser())])

    def _restic_forget_one(self, job: Job, snap: Dict[str, Any]):
        snap_id = snap.get("short_id") or str(snap.get("id", ""))[:8]
        resp = QtWidgets.QMessageBox.question(
            self, "Forget snapshot",
            f"Permanently forget snapshot {snap_id}?\n\n"
            "This removes the snapshot reference. Run 'Forget + Prune' (or "
            "'Stats') afterwards to actually reclaim storage.",
            QtWidgets.QMessageBox.Yes | QtWidgets.QMessageBox.No,
            QtWidgets.QMessageBox.No,
        )
        if resp != QtWidgets.QMessageBox.Yes:
            return
        base, env = commands.restic_base_args(job)
        self._run_steps([RunStep(0, f"restic forget {snap_id}", "restic",
                                 base + ["forget", snap_id], env, PlainProgressParser())])

    def restic_forget_prune(self):
        job = self._current_restic_job()
        if not job:
            return
        try:
            dry_args, env = commands.restic_forget_args(job, prune=False, dry_run=True)
        except ValueError as e:
            QtWidgets.QMessageBox.warning(self, "Retention", str(e))
            return
        self._restic_capture_async(
            "Forget dry-run", dry_args, env,
            lambda rc, out, err: self._on_forget_dry_run(job, env, rc, out, err))

    def _on_forget_dry_run(self, job: Job, env: Dict[str, str],
                           rc: int, out: str, err: str):
        if rc != 0:
            self._show_restic_error("Forget (dry-run)", rc, out, err)
            return
        preview = (out or "").strip() or "(restic reported nothing to remove)"
        box = QtWidgets.QMessageBox(self)
        box.setIcon(QtWidgets.QMessageBox.Warning)
        box.setWindowTitle("Forget + Prune — dry run")
        box.setText(
            "Apply the retention policy and PRUNE (permanently delete) "
            "unreferenced data?\n\nDry-run details are below.")
        box.setDetailedText(preview)
        box.setStandardButtons(QtWidgets.QMessageBox.Yes | QtWidgets.QMessageBox.No)
        box.setDefaultButton(QtWidgets.QMessageBox.No)
        if box.exec_() != QtWidgets.QMessageBox.Yes:
            return
        real_args, _ = commands.restic_forget_args(job, prune=True)
        self._run_steps([RunStep(0, f"{job.name}: forget + prune", "restic",
                                 real_args, env, PlainProgressParser())])

    def restic_stats(self):
        job = self._current_restic_job()
        if not job:
            return
        try:
            base, env = commands.restic_base_args(job)
        except ValueError as e:
            QtWidgets.QMessageBox.warning(self, "Invalid configuration", str(e))
            return
        self._restic_capture_async(
            "Reading stats", base + ["stats", "--json"], env,
            lambda rc, out, err: self._on_stats_restore_size(job, base, env, rc, out, err))

    def _on_stats_restore_size(self, job: Job, base: List[str],
                               env: Dict[str, str], rc: int, out: str, err: str):
        if rc != 0:
            self._show_restic_error("Stats", rc, out, err)
            return
        try:
            restore = json.loads(out or "{}")
        except (ValueError, json.JSONDecodeError):
            restore = {}
        self._restic_capture_async(
            "Reading raw stats", base + ["stats", "--mode", "raw-data", "--json"], env,
            lambda rc2, out2, _err2: self._show_stats(job, restore, rc2, out2))

    def _show_stats(self, job: Job, restore: Dict[str, Any], rc2: int, out2: str):
        raw = {}
        if rc2 == 0:
            try:
                raw = json.loads(out2 or "{}")
            except (ValueError, json.JSONDecodeError):
                raw = {}
        lines = []
        if restore.get("total_size") is not None:
            lines.append(f"Restore size (logical): {human_bytes(restore.get('total_size', 0))}")
        if restore.get("total_file_count") is not None:
            lines.append(f"Files: {restore.get('total_file_count')}")
        if raw.get("total_size") is not None:
            lines.append(f"Raw data stored: {human_bytes(raw.get('total_size', 0))}")
            ls = restore.get("total_size") or 0
            rs = raw.get("total_size") or 0
            if ls and rs:
                lines.append(f"Dedup/compression: {(1 - rs / ls) * 100:.1f}% smaller")
        QtWidgets.QMessageBox.information(
            self, f"Stats — {job.name}", "\n".join(lines) or "(no data returned)")

    def append_log(self, text: str):
        if text is None:
            return
        self.log.appendPlainText(text)
        cursor = self.log.textCursor()
        cursor.movePosition(QtGui.QTextCursor.End)
        self.log.setTextCursor(cursor)

    def on_progress(self, pct: int):
        self.progress.setValue(pct)

    def on_progress_line(self, text: str):
        self.statusBar().showMessage(text)

    def on_started(self):
        self.statusBar().showMessage("Running…")

    def on_state(self, state: str):
        self.statusBar().showMessage(f"State: {state}")

    def _update_buttons(self, *, running: bool):
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
        self.rb_rsync.setEnabled(not running)
        self.rb_restic.setEnabled(not running)
        self.btn_res_test.setEnabled(not running)
        self.btn_res_init.setEnabled(not running)
        self.btn_res_snap.setEnabled(not running)
        self.btn_res_check.setEnabled(not running)
        self.btn_res_unlock.setEnabled(not running)
        self.btn_res_prune.setEnabled(not running)
        self.btn_res_stats.setEnabled(not running)
        self.btn_res_export.setEnabled(not running)

    def load_settings(self):
        path = settings.config_file_path()
        if not path.exists():
            return
        try:
            raw = path.read_text()
        except OSError as e:
            QtWidgets.QMessageBox.warning(self, "Settings load error", str(e))
            return
        try:
            data = settings.parse_settings_text(raw)
        except ValueError as e:
            # Preserve the broken file before auto-save / save-on-close
            # rewrites settings.json and destroys whatever is recoverable.
            bak = settings.write_corrupt_backup(path, raw)
            note = f"\n\nThe unparseable file was backed up to:\n{bak}" if bak else ""
            QtWidgets.QMessageBox.warning(
                self, "Settings load error",
                f"settings.json could not be parsed and will be replaced on "
                f"the next save.\n\n{e}{note}")
            return
        # One-time backup of the pre-v2 file before we migrate/rewrite it.
        if settings.is_legacy(data):
            settings.write_legacy_backup(path, raw)
        # Drop the selection before swapping the model out, so the next
        # _select_job() doesn't collect the stale on-screen form into the
        # freshly loaded job at the old index.
        self.current_job_index = -1
        self.jobs = settings.jobs_from_data(data)
        self._refresh_jobs_list()
        geometry = settings.window_geometry_from_data(data)
        if geometry:
            try:
                self.restoreGeometry(QtCore.QByteArray.fromBase64(geometry.encode("ascii")))
            except (UnicodeEncodeError, ValueError):
                pass  # hand-edited/corrupted blob — keep the default geometry
        else:
            size = settings.window_size_from_data(data)  # legacy pre-geometry configs
            if size:
                self.resize(size[0], size[1])

    def save_settings(self):
        if 0 <= self.current_job_index < len(self.jobs):
            self._collect_form_into_job(self.jobs[self.current_job_index])
        path = settings.config_file_path()
        window = {"geometry": bytes(self.saveGeometry().toBase64()).decode("ascii")}
        data = settings.build_settings_dict(self.jobs, window)
        try:
            settings.write_settings(path, data)
            self.statusBar().showMessage(f"Saved settings → {path}", 3000)
        except Exception as e:
            QtWidgets.QMessageBox.warning(self, "Settings save error", str(e))

    def reload_settings(self):
        self.load_settings()
        if not self.jobs:
            self._add_default_job()
        self._select_job(0)

    def closeEvent(self, e: QtGui.QCloseEvent):
        if self.runner.proc.state() != QtCore.QProcess.NotRunning:
            resp = QtWidgets.QMessageBox.question(
                self,
                "Backup in progress",
                "A backup is still running. Stop it and quit?",
                QtWidgets.QMessageBox.Yes | QtWidgets.QMessageBox.No,
                QtWidgets.QMessageBox.No,
            )
            if resp != QtWidgets.QMessageBox.Yes:
                e.ignore()
                return
            self._steps.clear()
            self.runner.stop()
        if self.capture.running():
            # A metadata read (snapshots/stats/…) is harmless to kill.
            self._capture_cancelled = True
            self.capture.cancel()
        self._save_timer.stop()  # cancel any pending debounced save
        try:
            self.save_settings()
        finally:
            super().closeEvent(e)


def main():
    app = QtWidgets.QApplication(sys.argv)
    app.setApplicationName("Qrabackup")
    app.setOrganizationName("qrabackup")

    font = app.font()
    if sys.platform.startswith("linux"):
        font.setPointSize(10)
        app.setFont(font)

    w = MainWindow()
    w.show()
    sys.exit(app.exec_())
