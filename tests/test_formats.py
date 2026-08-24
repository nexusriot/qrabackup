"""Tests for qrabackup_app.formats (pure, no Qt)."""
import unittest

from qrabackup_app import formats
from qrabackup_app.model import LastRun


class HumanBytesTest(unittest.TestCase):
    def test_bytes_under_kib(self):
        self.assertEqual(formats.human_bytes(0), "0 B")
        self.assertEqual(formats.human_bytes(512), "512 B")
        self.assertEqual(formats.human_bytes(1023), "1023 B")

    def test_scales(self):
        self.assertEqual(formats.human_bytes(1024), "1.0 KiB")
        self.assertEqual(formats.human_bytes(1536), "1.5 KiB")
        self.assertEqual(formats.human_bytes(1048576), "1.0 MiB")
        self.assertEqual(formats.human_bytes(1073741824), "1.0 GiB")

    def test_non_number_passthrough(self):
        self.assertEqual(formats.human_bytes("n/a"), "n/a")
        self.assertEqual(formats.human_bytes(None), "None")


class FmtEtaTest(unittest.TestCase):
    def test_minutes_seconds(self):
        self.assertEqual(formats.fmt_eta(0), "0:00")
        self.assertEqual(formats.fmt_eta(90), "1:30")
        self.assertEqual(formats.fmt_eta(59), "0:59")

    def test_hours(self):
        self.assertEqual(formats.fmt_eta(3661), "1:01:01")

    def test_bad_input(self):
        self.assertEqual(formats.fmt_eta(None), "?")
        self.assertEqual(formats.fmt_eta("x"), "?")


class FormatResticStatusTest(unittest.TestCase):
    def test_full_status(self):
        msg = {"percent_done": 0.42, "bytes_done": 1048576, "total_bytes": 2097152,
               "files_done": 3, "total_files": 10, "seconds_remaining": 90}
        s = formats.format_restic_status(msg)
        self.assertIn("42%", s)
        self.assertIn("1.0 MiB/2.0 MiB", s)
        self.assertIn("3/10 files", s)
        self.assertIn("ETA 1:30", s)

    def test_minimal_status(self):
        # No totals -> just the percent, no spurious sections.
        s = formats.format_restic_status({"percent_done": 0.0})
        self.assertEqual(s, "0%")


class FormatResticSummaryTest(unittest.TestCase):
    def test_summary_fields(self):
        msg = {"snapshot_id": "abcdef1234567890", "files_new": 5, "files_changed": 2,
               "files_unmodified": 100, "data_added": 1234567,
               "total_bytes_processed": 7654321, "total_duration": 3.25}
        s = formats.format_restic_summary(msg)
        self.assertIn("Snapshot abcdef12 saved", s)  # truncated to 8 chars
        self.assertIn("5 new", s)
        self.assertIn("2 changed", s)
        self.assertIn("100 unmodified", s)
        self.assertIn("in 3.2s", s)

    def test_missing_snapshot_id(self):
        s = formats.format_restic_summary({})
        self.assertIn("(unknown)", s)


class FmtDurationTest(unittest.TestCase):
    def test_seconds(self):
        self.assertEqual(formats.fmt_duration(0), "0s")
        self.assertEqual(formats.fmt_duration(5.4), "5s")
        self.assertEqual(formats.fmt_duration(59), "59s")

    def test_minutes(self):
        self.assertEqual(formats.fmt_duration(75), "1m15s")
        self.assertEqual(formats.fmt_duration(600), "10m00s")

    def test_hours(self):
        self.assertEqual(formats.fmt_duration(3661), "1h01m")

    def test_bad_input(self):
        self.assertEqual(formats.fmt_duration("x"), "?")
        self.assertEqual(formats.fmt_duration(None), "?")
        self.assertEqual(formats.fmt_duration(-1), "?")


class FormatLastRunTest(unittest.TestCase):
    def test_never_run(self):
        self.assertEqual(formats.format_last_run(None), "")
        self.assertEqual(formats.format_last_run(LastRun()), "")

    def test_success(self):
        s = formats.format_last_run(
            LastRun(when="2026-08-24T12:00:33", exit_code=0, duration_s=3.2))
        self.assertEqual(s, "✓ 2026-08-24 12:00 · 3s")

    def test_failure_shows_exit_code(self):
        s = formats.format_last_run(
            LastRun(when="2026-08-24T12:00:33", exit_code=23, duration_s=3.2))
        self.assertEqual(s, "✗ exit 23 · 2026-08-24 12:00")


if __name__ == "__main__":
    unittest.main()
