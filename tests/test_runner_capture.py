"""CaptureRunner tests — real subprocesses driven on an offscreen Qt event
loop (no display needed). A full QApplication is used (not QCoreApplication)
so this module shares the singleton with the UI tests in the same process."""
import os
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt5 import QtCore, QtWidgets

from qrabackup_app.runner import CaptureRunner


class CaptureRunnerTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])

    def _run(self, program, args, env=None, timeout_ms=10000, cancel_after_ms=None):
        runner = CaptureRunner()
        result = []
        loop = QtCore.QEventLoop()
        runner.done.connect(lambda rc, out, err: (result.append((rc, out, err)),
                                                  loop.quit()))
        guard = QtCore.QTimer()
        guard.setSingleShot(True)
        guard.timeout.connect(loop.quit)
        guard.start(15000)
        runner.start(program, args, env, timeout_ms)
        if cancel_after_ms is not None:
            QtCore.QTimer.singleShot(cancel_after_ms, runner.cancel)
        loop.exec_()
        self.assertTrue(result, "capture did not finish")
        return result[0]

    def test_captures_output_and_exit_code(self):
        rc, out, err = self._run("sh", ["-c", "echo OUT; echo ERR >&2; exit 3"])
        self.assertEqual(rc, 3)
        self.assertEqual(out, "OUT\n")
        self.assertEqual(err, "ERR\n")

    def test_channels_stay_separate(self):
        # stdout must remain clean for JSON parsing even with stderr noise.
        rc, out, _err = self._run("sh", ["-c", "echo noise >&2; echo '{\"a\":1}'"])
        self.assertEqual(rc, 0)
        self.assertEqual(out, '{"a":1}\n')

    def test_env_is_passed(self):
        rc, out, _ = self._run("sh", ["-c", "printf '%s' \"$QRA_TEST_VAR\""],
                               env={"QRA_TEST_VAR": "hello"})
        self.assertEqual(rc, 0)
        self.assertEqual(out, "hello")

    def test_missing_program_reports_127(self):
        rc, _out, err = self._run("qra-definitely-not-a-real-program", [])
        self.assertEqual(rc, 127)
        self.assertIn("not found", err)

    def test_timeout_kills_and_reports_124(self):
        rc, _out, err = self._run("sh", ["-c", "sleep 30"], timeout_ms=300)
        self.assertEqual(rc, 124)
        self.assertIn("timed out", err)

    def test_cancel_reports_130(self):
        rc, _out, _err = self._run("sh", ["-c", "sleep 30"], cancel_after_ms=150)
        self.assertEqual(rc, 130)


if __name__ == "__main__":
    unittest.main()
