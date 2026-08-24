"""Tests for qrabackup_app.model (pure, no Qt)."""
import unittest

from qrabackup_app.model import (
    Job,
    LastRun,
    ResticConfig,
    ResticRetention,
    RsyncOptions,
    new_job,
)


class RsyncOptionsTest(unittest.TestCase):
    def test_defaults(self):
        o = RsyncOptions()
        self.assertTrue(o.archive)
        self.assertTrue(o.progress)
        self.assertFalse(o.delete)

    def test_from_dict_partial(self):
        o = RsyncOptions.from_dict({"archive": False, "delete": True, "unknown": 1})
        self.assertFalse(o.archive)
        self.assertTrue(o.delete)
        self.assertTrue(o.progress)  # untouched default

    def test_from_dict_coerces_bool(self):
        o = RsyncOptions.from_dict({"verbose": 1, "compress": 0})
        self.assertIs(o.verbose, True)
        self.assertIs(o.compress, False)


class ResticRetentionTest(unittest.TestCase):
    def test_defaults(self):
        r = ResticRetention()
        self.assertFalse(r.enabled)
        self.assertEqual(r.keep_daily, 7)
        self.assertEqual(r.keep_weekly, 4)
        self.assertEqual(r.keep_monthly, 6)
        self.assertTrue(r.prune)

    def test_from_dict(self):
        r = ResticRetention.from_dict({"enabled": True, "keep_daily": 3, "prune": False})
        self.assertTrue(r.enabled)
        self.assertEqual(r.keep_daily, 3)
        self.assertFalse(r.prune)

    def test_from_dict_bad_int_ignored(self):
        r = ResticRetention.from_dict({"keep_daily": "oops"})
        self.assertEqual(r.keep_daily, 7)  # falls back to default


class ResticConfigRepoTest(unittest.TestCase):
    def test_aws_region_and_prefix(self):
        c = ResticConfig(bucket="my-bucket", region="eu-central-1", prefix="laptop")
        self.assertEqual(c.build_repository(),
                         "s3:s3.eu-central-1.amazonaws.com/my-bucket/laptop")

    def test_aws_default_region(self):
        c = ResticConfig(bucket="my-bucket")
        self.assertEqual(c.build_repository(), "s3:s3.amazonaws.com/my-bucket")

    def test_custom_endpoint(self):
        c = ResticConfig(bucket="b", endpoint="https://minio.local:9000", prefix="/sub/")
        self.assertEqual(c.build_repository(), "s3:https://minio.local:9000/b/sub")

    def test_raw_override_wins(self):
        c = ResticConfig(bucket="ignored", region="us-east-1", repository="s3:custom/raw")
        self.assertEqual(c.build_repository(), "s3:custom/raw")

    def test_empty_when_no_bucket(self):
        self.assertEqual(ResticConfig().build_repository(), "")

    def test_strips_slashes(self):
        c = ResticConfig(bucket="/b/", prefix="/p/")
        self.assertEqual(c.build_repository(), "s3:s3.amazonaws.com/b/p")


class ResticConfigEnvTest(unittest.TestCase):
    def test_full_env(self):
        c = ResticConfig(region="us-east-1", password="pw",
                         access_key_id="AKIA", secret_access_key="sek")
        env = c.build_env()
        self.assertEqual(env, {
            "RESTIC_PASSWORD": "pw",
            "AWS_ACCESS_KEY_ID": "AKIA",
            "AWS_SECRET_ACCESS_KEY": "sek",
            "AWS_DEFAULT_REGION": "us-east-1",
        })

    def test_empty_values_omitted(self):
        self.assertEqual(ResticConfig().build_env(), {})

    def test_roundtrip(self):
        c = ResticConfig(bucket="b", tags=["a", "b"],
                         retention=ResticRetention(enabled=True, keep_daily=2))
        c2 = ResticConfig.from_dict(c.to_dict())
        self.assertEqual(c2.bucket, "b")
        self.assertEqual(c2.tags, ["a", "b"])
        self.assertTrue(c2.retention.enabled)
        self.assertEqual(c2.retention.keep_daily, 2)


class JobTest(unittest.TestCase):
    def test_v1_migration(self):
        # v1: rsync flags under "options", no engine/id/restic.
        v1 = {"name": "Docs", "sources": ["/a"], "destination": "/b",
              "excludes": ["*.tmp"], "options": {"archive": True, "delete": True}}
        j = Job.from_dict(v1)
        self.assertEqual(j.engine, "rsync")
        self.assertTrue(j.rsync.delete)
        self.assertEqual(j.excludes, ["*.tmp"])
        self.assertTrue(j.id)  # generated
        self.assertEqual(j.restic.build_repository(), "")  # empty restic block

    def test_roundtrip_v2(self):
        j = new_job("X", engine="restic")
        j.sources = ["/src"]
        j.restic.bucket = "bk"
        j2 = Job.from_dict(j.to_dict())
        self.assertEqual(j2.name, "X")
        self.assertEqual(j2.engine, "restic")
        self.assertEqual(j2.id, j.id)  # id preserved on roundtrip
        self.assertEqual(j2.sources, ["/src"])
        self.assertEqual(j2.restic.bucket, "bk")

    def test_new_job_defaults(self):
        j = new_job("Default")
        self.assertEqual(j.engine, "rsync")
        self.assertEqual(j.sources, [])
        self.assertTrue(j.id)
        # two new jobs get distinct ids
        self.assertNotEqual(j.id, new_job("Other").id)


class LastRunTest(unittest.TestCase):
    def test_roundtrip_via_job(self):
        j = new_job("A")
        j.last_run = LastRun(when="2026-08-24T12:00:00", exit_code=0, duration_s=3.2)
        d = j.to_dict()
        self.assertEqual(d["last_run"],
                         {"when": "2026-08-24T12:00:00", "exit_code": 0, "duration_s": 3.2})
        j2 = Job.from_dict(d)
        self.assertEqual(j2.last_run.when, "2026-08-24T12:00:00")
        self.assertEqual(j2.last_run.exit_code, 0)
        self.assertEqual(j2.last_run.duration_s, 3.2)

    def test_absent_by_default(self):
        j = Job.from_dict({"name": "A"})
        self.assertIsNone(j.last_run)
        self.assertNotIn("last_run", j.to_dict())

    def test_non_dict_ignored(self):
        self.assertIsNone(Job.from_dict({"name": "A", "last_run": "junk"}).last_run)

    def test_bad_values_fall_back(self):
        lr = LastRun.from_dict({"when": 5, "exit_code": "x", "duration_s": None})
        self.assertEqual(lr.when, "5")
        self.assertEqual(lr.exit_code, 0)
        self.assertEqual(lr.duration_s, 0.0)


if __name__ == "__main__":
    unittest.main()
