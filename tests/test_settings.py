"""Tests for qrabackup_app.settings (pure logic + atomic write, no Qt)."""
import json
import os
import shutil
import stat
import tempfile
import unittest
from pathlib import Path

from qrabackup_app import settings
from qrabackup_app.model import new_job


class DataParsingTest(unittest.TestCase):
    def test_is_legacy(self):
        self.assertTrue(settings.is_legacy({}))
        self.assertTrue(settings.is_legacy({"version": 1}))
        self.assertFalse(settings.is_legacy({"version": settings.SETTINGS_VERSION}))

    def test_jobs_from_data(self):
        data = {"jobs": [{"name": "A", "options": {"delete": True}},
                         {"name": "B", "engine": "restic"}]}
        jobs = settings.jobs_from_data(data)
        self.assertEqual([j.name for j in jobs], ["A", "B"])
        self.assertTrue(jobs[0].rsync.delete)
        self.assertEqual(jobs[1].engine, "restic")

    def test_jobs_from_empty(self):
        self.assertEqual(settings.jobs_from_data({}), [])

    def test_window_size_legacy(self):
        self.assertEqual(settings.window_size_from_data({"window": {"size": [800, 600]}}),
                         (800, 600))
        self.assertIsNone(settings.window_size_from_data({}))
        self.assertIsNone(settings.window_size_from_data({"window": {"size": [1]}}))
        self.assertIsNone(settings.window_size_from_data({"window": {"size": "bad"}}))

    def test_parse_settings_text(self):
        self.assertEqual(settings.parse_settings_text('{"version": 2}'), {"version": 2})
        with self.assertRaises(ValueError):
            settings.parse_settings_text("{not json")
        with self.assertRaises(ValueError):
            settings.parse_settings_text("[1, 2]")  # valid JSON, wrong root
        with self.assertRaises(ValueError):
            settings.parse_settings_text('"just a string"')

    def test_window_geometry(self):
        self.assertEqual(
            settings.window_geometry_from_data({"window": {"geometry": "AQID"}}), "AQID")
        self.assertIsNone(settings.window_geometry_from_data({}))
        self.assertIsNone(settings.window_geometry_from_data({"window": {}}))
        self.assertIsNone(settings.window_geometry_from_data({"window": {"geometry": ""}}))
        self.assertIsNone(settings.window_geometry_from_data({"window": {"geometry": 123}}))


class BuildSettingsTest(unittest.TestCase):
    def test_build_dict(self):
        jobs = [new_job("A"), new_job("B", engine="restic")]
        d = settings.build_settings_dict(jobs, {"geometry": "AQID"})
        self.assertEqual(d["version"], settings.SETTINGS_VERSION)
        self.assertEqual(len(d["jobs"]), 2)
        self.assertEqual(d["window"], {"geometry": "AQID"})

    def test_build_dict_no_window(self):
        d = settings.build_settings_dict([new_job("A")])
        self.assertNotIn("window", d)

    def test_build_dict_window_roundtrips_to_geometry_reader(self):
        d = settings.build_settings_dict([new_job("A")], {"geometry": "Zm9v"})
        self.assertEqual(settings.window_geometry_from_data(d), "Zm9v")


class WriteSettingsTest(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix="qra-settings-test-")
        self.path = Path(self.dir) / "settings.json"

    def tearDown(self):
        shutil.rmtree(self.dir, ignore_errors=True)

    def test_atomic_write_roundtrip(self):
        jobs = [new_job("A")]
        data = settings.build_settings_dict(jobs, {"geometry": "AQID"})
        settings.write_settings(self.path, data)
        self.assertTrue(self.path.exists())
        loaded = json.loads(self.path.read_text())
        self.assertEqual(loaded["version"], settings.SETTINGS_VERSION)
        self.assertEqual(loaded["jobs"][0]["name"], "A")
        self.assertEqual(settings.window_geometry_from_data(loaded), "AQID")
        # no leftover temp file
        self.assertFalse((Path(self.dir) / "settings.tmp").exists())

    def test_owner_only_perms(self):
        settings.write_settings(self.path, {"version": 2, "jobs": []})
        mode = stat.S_IMODE(os.stat(self.path).st_mode)
        self.assertEqual(mode, 0o600)

    def test_rewrite_tightens_loose_perms(self):
        self.path.write_text("{}")
        os.chmod(self.path, 0o644)
        settings.write_settings(self.path, {"version": 2, "jobs": []})
        self.assertEqual(stat.S_IMODE(os.stat(self.path).st_mode), 0o600)

    def test_corrupt_backup_once(self):
        raw = "{broken"
        self.path.write_text(raw)
        bak = settings.write_corrupt_backup(self.path, raw)
        self.assertIsNotNone(bak)
        self.assertEqual(bak.name, "settings.json.corrupt.bak")
        self.assertEqual(bak.read_text(), raw)
        self.assertEqual(stat.S_IMODE(os.stat(bak).st_mode), 0o600)
        # second call is a no-op (does not overwrite)
        self.assertIsNone(settings.write_corrupt_backup(self.path, "other"))
        self.assertEqual(bak.read_text(), raw)

    def test_legacy_backup_once(self):
        self.path.write_text('{"jobs": []}')  # legacy file
        bak = settings.write_legacy_backup(self.path, self.path.read_text())
        self.assertIsNotNone(bak)
        self.assertTrue(bak.exists())
        self.assertEqual(bak.name, "settings.json.v1.bak")
        # second call is a no-op (does not overwrite)
        self.assertIsNone(settings.write_legacy_backup(self.path, "different"))
        self.assertEqual(bak.read_text(), '{"jobs": []}')


if __name__ == "__main__":
    unittest.main()
