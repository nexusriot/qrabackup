"""Tests for qrabackup_app.parsers (pure, no Qt)."""
import json
import unittest

from qrabackup_app.parsers import (
    Emit,
    PlainProgressParser,
    ResticJsonProgressParser,
    RsyncProgressParser,
)


class EmitTest(unittest.TestCase):
    def test_defaults_all_none(self):
        e = Emit()
        self.assertIsNone(e.percent)
        self.assertIsNone(e.status)
        self.assertIsNone(e.log)
        self.assertIsNone(e.error)


class PlainParserTest(unittest.TestCase):
    def setUp(self):
        self.p = PlainProgressParser()

    def test_newline_goes_to_log(self):
        e = self.p.feed("a line", True)
        self.assertEqual(e.log, "a line")
        self.assertIsNone(e.status)

    def test_cr_goes_to_status(self):
        e = self.p.feed("  in place  ", False)
        self.assertEqual(e.status, "in place")
        self.assertIsNone(e.log)

    def test_empty_cr_no_status(self):
        self.assertIsNone(self.p.feed("   ", False).status)


class RsyncParserTest(unittest.TestCase):
    def setUp(self):
        self.p = RsyncProgressParser()

    def test_percent(self):
        self.assertEqual(self.p.feed("   1,234  57%  1.2MB/s", False).percent, 57)

    def test_to_chk(self):
        self.assertEqual(self.p.feed("file (to-chk=25/100)", True).percent, 75)

    def test_ir_chk(self):
        self.assertEqual(self.p.feed("x (ir-chk=0/4)", True).percent, 100)

    def test_newline_logs_text(self):
        e = self.p.feed("sending incremental file list", True)
        self.assertEqual(e.log, "sending incremental file list")

    def test_no_percent(self):
        self.assertIsNone(self.p.feed("just text", True).percent)


class ResticJsonParserTest(unittest.TestCase):
    def setUp(self):
        self.p = ResticJsonProgressParser()

    def test_status(self):
        e = self.p.feed(json.dumps({
            "message_type": "status", "percent_done": 0.42,
            "bytes_done": 1, "total_bytes": 2, "files_done": 1, "total_files": 2,
        }), True)
        self.assertEqual(e.percent, 42)
        self.assertIn("42%", e.status)
        self.assertIsNone(e.log)

    def test_summary(self):
        e = self.p.feed(json.dumps({
            "message_type": "summary", "snapshot_id": "abcdef1234",
            "files_new": 5, "data_added": 100, "total_duration": 1.0,
        }), True)
        self.assertEqual(e.percent, 100)
        self.assertTrue(e.log.startswith("Snapshot abcdef12 saved"))

    def test_error(self):
        e = self.p.feed(json.dumps({
            "message_type": "error", "error": {"message": "boom"}, "item": "/x",
        }), True)
        self.assertTrue(e.error.startswith("restic error: boom"))
        self.assertIn("/x", e.error)

    def test_non_json_to_log(self):
        e = self.p.feed("Warning: not json", True)
        self.assertEqual(e.log, "Warning: not json")

    def test_non_json_cr_to_status(self):
        e = self.p.feed("redraw", False)
        self.assertEqual(e.status, "redraw")

    def test_empty_ignored(self):
        e = self.p.feed("   ", True)
        self.assertIsNone(e.log)
        self.assertIsNone(e.status)

    def test_non_dict_json_ignored(self):
        e = self.p.feed("[1, 2, 3]", True)
        self.assertIsNone(e.log)
        self.assertIsNone(e.percent)

    def test_verbose_status_ignored(self):
        e = self.p.feed(json.dumps({"message_type": "verbose_status"}), True)
        self.assertIsNone(e.percent)
        self.assertIsNone(e.log)


if __name__ == "__main__":
    unittest.main()
