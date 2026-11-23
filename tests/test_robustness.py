"""Regression tests for review fixes: malformed settings must not crash
startup, backups are created owner-only, restic JSON nulls are tolerated, and
the snapshot dialog defaults to the newest snapshot."""
import json
import os
import shutil
import stat
import tempfile
import unittest
from pathlib import Path
from unittest import mock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from qrabackup_app import settings
from qrabackup_app.formats import format_restic_status, format_restic_summary
from qrabackup_app.model import Job
from qrabackup_app.parsers import ResticJsonProgressParser


class MalformedModelTest(unittest.TestCase):
    def test_null_sections(self):
        j = Job.from_dict({"name": None, "restic": None, "rsync": None,
                           "last_run": None, "sources": None, "excludes": None})
        self.assertEqual(j.name, "Job")
        self.assertEqual(j.sources, [])
        self.assertEqual(j.restic.tags, [])
        self.assertEqual(j.restic.password, "")

    def test_string_sources_not_split_into_chars(self):
        j = Job.from_dict({"sources": "/home/me", "excludes": "*.tmp"})
        self.assertEqual(j.sources, [])
        self.assertEqual(j.excludes, [])

    def test_null_tags_and_retention(self):
        j = Job.from_dict({"restic": {"tags": None, "retention": None, "bucket": None}})
        self.assertEqual(j.restic.tags, [])
        self.assertEqual(j.restic.bucket, "")
        self.assertFalse(j.restic.retention.enabled)

    def test_unknown_engine_falls_back_to_rsync(self):
        self.assertEqual(Job.from_dict({"engine": "Restic"}).engine, "restic")
        self.assertEqual(Job.from_dict({"engine": "borg"}).engine, "rsync")

    def test_jobs_not_a_list_is_an_error(self):
        with self.assertRaises(ValueError):
            settings.jobs_from_data({"jobs": "x"})

    def test_non_object_job_entries_skipped(self):
        jobs = settings.jobs_from_data({"jobs": [None, 3, {"name": "ok"}]})
        self.assertEqual([j.name for j in jobs], ["ok"])

    def test_window_wrong_type(self):
        self.assertIsNone(settings.window_geometry_from_data({"window": []}))
        self.assertIsNone(settings.window_size_from_data({"window": "x"}))


@unittest.skipIf(os.name == "nt", "POSIX permissions")
class BackupPermsTest(unittest.TestCase):
    def test_corrupt_backup_created_owner_only_even_with_loose_umask(self):
        d = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, d, ignore_errors=True)
        old = os.umask(0o022)
        try:
            bak = settings.write_corrupt_backup(Path(d) / "settings.json", "secret")
        finally:
            os.umask(old)
        self.assertEqual(stat.S_IMODE(os.stat(bak).st_mode), 0o600)
        self.assertEqual(bak.read_text(), "secret")


class ResticNullFieldsTest(unittest.TestCase):
    def test_status_with_nulls(self):
        e = ResticJsonProgressParser().feed(
            json.dumps({"message_type": "status", "percent_done": None}), True)
        self.assertEqual(e.percent, 0)
        self.assertTrue(format_restic_status({"percent_done": None}).startswith("0%"))

    def test_summary_with_null_duration(self):
        self.assertIn("0.0s", format_restic_summary({"total_duration": None}))


class StartupWithMalformedSettingsTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from PyQt5 import QtWidgets
        cls.QtWidgets = QtWidgets
        cls.app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])

    def setUp(self):
        self.cfg_dir = tempfile.mkdtemp(prefix="qra-robust-")
        self.addCleanup(shutil.rmtree, self.cfg_dir, ignore_errors=True)
        patcher = mock.patch.dict(os.environ, {"XDG_CONFIG_HOME": self.cfg_dir})
        patcher.start()
        self.addCleanup(patcher.stop)

    def _start_with(self, data):
        settings.config_file_path().write_text(json.dumps(data))
        from qrabackup_app.ui import MainWindow
        with mock.patch.object(self.QtWidgets.QMessageBox, "warning") as warn:
            w = MainWindow()
        w._save_timer.stop()
        self.addCleanup(w.deleteLater)
        return w, warn

    def test_jobs_not_a_list_backs_up_and_starts(self):
        w, warn = self._start_with({"version": 2, "jobs": "garbage"})
        self.assertEqual([j.name for j in w.jobs], ["Default"])
        warn.assert_called_once()
        bak = Path(self.cfg_dir) / "qrabackup" / "settings.json.corrupt.bak"
        self.assertTrue(bak.exists())

    def test_null_restic_section_loads(self):
        w, warn = self._start_with({"version": 2, "jobs": [{"name": "A", "restic": None}]})
        self.assertEqual([j.name for j in w.jobs], ["A"])
        warn.assert_not_called()


class SnapshotsDialogOrderTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from PyQt5 import QtWidgets
        cls.app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])

    def test_newest_first_and_selected(self):
        from qrabackup_app.ui import SnapshotsDialog
        snaps = [{"short_id": "old", "time": "2024-01-01T00:00:00Z"},
                 {"short_id": "new", "time": "2025-06-01T00:00:00Z"}]
        dlg = SnapshotsDialog(None, snaps)
        self.addCleanup(dlg.deleteLater)
        self.assertEqual(dlg._selected()["short_id"], "new")


if __name__ == "__main__":
    unittest.main()
