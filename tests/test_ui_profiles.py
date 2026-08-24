"""UI regression tests for profile list operations (offscreen Qt, no display).

These exercise MainWindow's job-list handlers, which mix Qt selection signals
with the form-collect/form-load cycle — historically the source of a bug where
deleting a profile clobbered the next profile with the deleted one's data.
"""
import os
import shutil
import tempfile
import time
import unittest
from unittest import mock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt5 import QtWidgets

from qrabackup_app.commands import RunStep
from qrabackup_app.model import LastRun, new_job


def _job(name: str, source: str, dest: str):
    j = new_job(name)
    j.sources = [source]
    j.destination = dest
    return j


class _MainWindowTest(unittest.TestCase):
    """Shared offscreen MainWindow fixture with a throwaway config dir."""

    @classmethod
    def setUpClass(cls):
        cls.app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])

    def setUp(self):
        # Point the config dir at a throwaway location BEFORE MainWindow reads it.
        self.cfg_dir = tempfile.mkdtemp(prefix="qra-ui-test-")
        self._old_xdg = os.environ.get("XDG_CONFIG_HOME")
        os.environ["XDG_CONFIG_HOME"] = self.cfg_dir
        from qrabackup_app.ui import MainWindow
        self.win = MainWindow()

    def tearDown(self):
        self.win.close()
        self.win.deleteLater()
        if self._old_xdg is None:
            os.environ.pop("XDG_CONFIG_HOME", None)
        else:
            os.environ["XDG_CONFIG_HOME"] = self._old_xdg
        shutil.rmtree(self.cfg_dir, ignore_errors=True)

    def _install_jobs(self, jobs):
        self.win.jobs = jobs
        self.win.current_job_index = -1
        self.win._refresh_jobs_list()
        self.win._select_job(0)


class DeleteJobTest(_MainWindowTest):
    def _delete_current(self):
        with mock.patch.object(QtWidgets.QMessageBox, "question",
                               return_value=QtWidgets.QMessageBox.Yes):
            self.win.delete_job()

    def test_delete_first_keeps_successor_data(self):
        # Regression: the deleted profile's on-screen form used to be collected
        # into the job that slid into its list position.
        self._install_jobs([_job("A", "/src/a", "/dst/a"),
                            _job("B", "/src/b", "/dst/b")])
        self._delete_current()
        self.assertEqual([j.name for j in self.win.jobs], ["B"])
        self.assertEqual(self.win.jobs[0].sources, ["/src/b"])
        self.assertEqual(self.win.jobs[0].destination, "/dst/b")
        # The surviving job is selected and its data is on screen.
        self.assertEqual(self.win.current_job_index, 0)
        self.assertEqual(self.win.dst_edit.text(), "/dst/b")

    def test_delete_middle_keeps_neighbors(self):
        self._install_jobs([_job("A", "/src/a", "/dst/a"),
                            _job("B", "/src/b", "/dst/b"),
                            _job("C", "/src/c", "/dst/c")])
        self.win._select_job(1)
        self._delete_current()
        self.assertEqual([j.name for j in self.win.jobs], ["A", "C"])
        self.assertEqual(self.win.jobs[0].destination, "/dst/a")
        self.assertEqual(self.win.jobs[1].destination, "/dst/c")

    def test_delete_last_selects_previous(self):
        self._install_jobs([_job("A", "/src/a", "/dst/a"),
                            _job("B", "/src/b", "/dst/b")])
        self.win._select_job(1)
        self._delete_current()
        self.assertEqual([j.name for j in self.win.jobs], ["A"])
        self.assertEqual(self.win.jobs[0].destination, "/dst/a")
        self.assertEqual(self.win.current_job_index, 0)

    def test_delete_sole_profile_refused(self):
        self._install_jobs([_job("A", "/src/a", "/dst/a")])
        with mock.patch.object(QtWidgets.QMessageBox, "information"):
            self.win.delete_job()
        self.assertEqual(len(self.win.jobs), 1)


class RunStatusTest(_MainWindowTest):
    def _finish_step(self, job, code, started_ago=2.0):
        self.win._current_step = RunStep(
            0, f"{job.name}: rsync", "rsync", [], None, None,
            job_id=job.id, records_status=True)
        self.win._step_started_at = time.monotonic() - started_ago
        self.win.on_step_finished(code)

    def test_backup_step_records_last_run(self):
        self._install_jobs([_job("A", "/src/a", "/dst/a")])
        job = self.win.jobs[0]
        self._finish_step(job, 23)
        self.assertIsNotNone(job.last_run)
        self.assertEqual(job.last_run.exit_code, 23)
        self.assertGreaterEqual(job.last_run.duration_s, 1.5)
        self.assertIn("✗ exit 23", self.win.jobs_list.item(0).text())
        # A later success overwrites the failure.
        self._finish_step(job, 0)
        self.assertEqual(job.last_run.exit_code, 0)
        self.assertIn("✓", self.win.jobs_list.item(0).text())

    def test_status_lands_on_the_right_profile(self):
        self._install_jobs([_job("A", "/src/a", "/dst/a"),
                            _job("B", "/src/b", "/dst/b")])
        job_b = self.win.jobs[1]
        self._finish_step(job_b, 0)
        self.assertIsNone(self.win.jobs[0].last_run)
        self.assertIsNotNone(job_b.last_run)
        self.assertIn("✓", self.win.jobs_list.item(1).text())
        self.assertNotIn("✓", self.win.jobs_list.item(0).text())

    def test_non_status_step_records_nothing(self):
        # Manual actions (init/check/unlock/restore) must not touch last_run.
        self._install_jobs([_job("A", "/src/a", "/dst/a")])
        job = self.win.jobs[0]
        self.win._current_step = RunStep(0, "restic check", "restic", [], None, None)
        self.win._step_started_at = time.monotonic()
        self.win.on_step_finished(1)
        self.assertIsNone(job.last_run)

    def test_duplicate_clears_last_run(self):
        self._install_jobs([_job("A", "/src/a", "/dst/a")])
        self.win.jobs[0].last_run = LastRun(when="2026-08-24T12:00:00",
                                            exit_code=0, duration_s=1.0)
        self.win.duplicate_job()
        self.assertEqual(len(self.win.jobs), 2)
        self.assertIsNone(self.win.jobs[1].last_run)
        self.assertIsNotNone(self.win.jobs[0].last_run)


if __name__ == "__main__":
    unittest.main()
