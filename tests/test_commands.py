"""Tests for qrabackup_app.commands (pure, no Qt)."""
import unittest

from qrabackup_app import commands
from qrabackup_app.model import ResticRetention, new_job
from qrabackup_app.parsers import (
    PlainProgressParser,
    ResticJsonProgressParser,
    RsyncProgressParser,
)


def _rsync_job(**kw):
    j = new_job("R")
    j.sources = ["/src"]
    j.destination = "/dst"
    for k, v in kw.items():
        setattr(j.rsync, k, v)
    return j


def _restic_job(**kw):
    j = new_job("S", engine="restic")
    j.sources = ["/src"]
    j.restic.bucket = "bk"
    j.restic.password = "pw"
    for k, v in kw.items():
        setattr(j.restic, k, v)
    return j


class RsyncArgsTest(unittest.TestCase):
    def test_basic(self):
        self.assertEqual(commands.rsync_args(_rsync_job()),
                         ["-a", "--info=progress2", "/src", "/dst"])

    def test_archive_off_keeps_recursion(self):
        args = commands.rsync_args(_rsync_job(archive=False, progress=False))
        self.assertIn("-r", args)
        self.assertNotIn("-a", args)

    def test_all_flags(self):
        args = commands.rsync_args(_rsync_job(
            verbose=True, compress=True, preserve=True, delete=True, dry_run=True))
        for flag in ("-v", "-z", "-p", "-o", "-g", "-D", "-t", "--delete", "--dry-run"):
            self.assertIn(flag, args)

    def test_excludes(self):
        j = _rsync_job()
        j.excludes = ["*.tmp", ".cache/"]
        args = commands.rsync_args(j)
        self.assertEqual(args.count("--exclude"), 2)

    def test_missing_sources_raises(self):
        j = _rsync_job()
        j.sources = []
        with self.assertRaises(ValueError):
            commands.rsync_args(j)

    def test_missing_destination_raises(self):
        j = _rsync_job()
        j.destination = ""
        with self.assertRaises(ValueError):
            commands.rsync_args(j)


class ResticBaseArgsTest(unittest.TestCase):
    def test_ok(self):
        args, env = commands.restic_base_args(_restic_job())
        self.assertEqual(args, ["-r", "s3:s3.amazonaws.com/bk"])
        self.assertEqual(env["RESTIC_PASSWORD"], "pw")

    def test_missing_repo_raises(self):
        j = _restic_job()
        j.restic.bucket = ""
        with self.assertRaises(ValueError):
            commands.restic_base_args(j)

    def test_missing_password_raises(self):
        j = _restic_job()
        j.restic.password = ""
        with self.assertRaises(ValueError):
            commands.restic_base_args(j)


class ResticBackupArgsTest(unittest.TestCase):
    def test_tags_excludes_sources(self):
        j = _restic_job(tags=["laptop"])
        j.excludes = ["*.log"]
        args, _ = commands.restic_backup_args(j)
        self.assertEqual(args, [
            "-r", "s3:s3.amazonaws.com/bk", "backup", "--json",
            "--tag", "laptop", "--exclude", "*.log", "/src",
        ])

    def test_missing_sources_raises(self):
        j = _restic_job()
        j.sources = []
        with self.assertRaises(ValueError):
            commands.restic_backup_args(j)


class ResticForgetArgsTest(unittest.TestCase):
    def test_keeps_tag_and_prune(self):
        j = _restic_job(tags=["laptop", "docs"],
                        retention=ResticRetention(enabled=True, keep_daily=7,
                                                  keep_weekly=4, keep_monthly=0,
                                                  prune=True))
        args, _ = commands.restic_forget_args(j, prune=True)
        self.assertEqual(args, [
            "-r", "s3:s3.amazonaws.com/bk", "forget",
            "--keep-daily", "7", "--keep-weekly", "4",
            "--tag", "laptop,docs", "--prune",
        ])

    def test_dry_run_has_no_prune(self):
        j = _restic_job(retention=ResticRetention(keep_daily=3))
        args, _ = commands.restic_forget_args(j, prune=False, dry_run=True)
        self.assertIn("--dry-run", args)
        self.assertNotIn("--prune", args)

    def test_prune_respects_retention_prune_flag(self):
        j = _restic_job(retention=ResticRetention(keep_daily=3, prune=False))
        args, _ = commands.restic_forget_args(j, prune=True)
        self.assertNotIn("--prune", args)

    def test_refuses_when_no_keeps(self):
        j = _restic_job(retention=ResticRetention(
            keep_last=0, keep_daily=0, keep_weekly=0, keep_monthly=0, keep_yearly=0))
        with self.assertRaises(ValueError):
            commands.restic_forget_args(j, prune=True)


class MaskedEnvTest(unittest.TestCase):
    def test_masks_secrets(self):
        env = {"RESTIC_PASSWORD": "pw", "AWS_ACCESS_KEY_ID": "AKIA12345",
               "AWS_SECRET_ACCESS_KEY": "sek", "AWS_DEFAULT_REGION": "eu-1"}
        s = commands.masked_env_str(env)
        self.assertIn("RESTIC_PASSWORD=***", s)
        self.assertIn("AWS_SECRET_ACCESS_KEY=***", s)
        self.assertIn("AWS_ACCESS_KEY_ID=AKIA…", s)  # id partially shown
        self.assertIn("AWS_DEFAULT_REGION=eu-1", s)  # non-secret shown


class PreviewCommandTest(unittest.TestCase):
    def test_rsync_preview(self):
        self.assertEqual(commands.preview_command(_rsync_job()),
                         "rsync -a --info=progress2 /src /dst")

    def test_restic_preview_masks_secret(self):
        s = commands.preview_command(_restic_job())
        self.assertIn("RESTIC_PASSWORD=***", s)
        self.assertIn("restic -r s3:s3.amazonaws.com/bk backup --json", s)
        self.assertNotIn("pw", s.split("restic", 1)[0])  # secret not leaked in env


class BuildRunStepsTest(unittest.TestCase):
    def test_rsync_single_step(self):
        steps = commands.build_run_steps(_rsync_job(), group=1)
        self.assertEqual(len(steps), 1)
        self.assertEqual(steps[0].program, "rsync")
        self.assertEqual(steps[0].group, 1)
        self.assertIsInstance(steps[0].parser, RsyncProgressParser)
        self.assertFalse(steps[0].abort_group_on_fail)

    def test_restic_backup_only_when_retention_disabled(self):
        steps = commands.build_run_steps(_restic_job(), group=0)
        self.assertEqual(len(steps), 1)
        self.assertIsInstance(steps[0].parser, ResticJsonProgressParser)
        self.assertTrue(steps[0].abort_group_on_fail)

    def test_restic_backup_plus_prune_when_enabled(self):
        j = _restic_job(retention=ResticRetention(enabled=True, keep_daily=3))
        steps = commands.build_run_steps(j, group=0)
        self.assertEqual(len(steps), 2)
        self.assertIn("backup", steps[0].label)
        self.assertIn("forget", steps[1].label)
        self.assertEqual(steps[1].args[-1], "--prune")
        self.assertIsInstance(steps[1].parser, PlainProgressParser)

    def test_retention_enabled_but_empty_raises(self):
        j = _restic_job(retention=ResticRetention(
            enabled=True, keep_daily=0, keep_weekly=0, keep_monthly=0,
            keep_last=0, keep_yearly=0))
        with self.assertRaises(ValueError):
            commands.build_run_steps(j, group=0)


class StepsToSkipTest(unittest.TestCase):
    def _steps(self):
        return [
            commands.RunStep(0, "backup", "restic", [], None, None, abort_group_on_fail=True),
            commands.RunStep(0, "prune", "restic", [], None, None),
            commands.RunStep(1, "other", "rsync", [], None, None),
        ]

    def test_abort_step_failure_skips_same_group(self):
        steps = self._steps()
        failed = steps[0]
        remaining = steps[1:]
        skip = commands.steps_to_skip_on_failure(failed, remaining, code=1)
        self.assertEqual([s.label for s in skip], ["prune"])  # only same group

    def test_success_skips_nothing(self):
        steps = self._steps()
        skip = commands.steps_to_skip_on_failure(steps[0], steps[1:], code=0)
        self.assertEqual(skip, [])

    def test_non_abort_step_skips_nothing(self):
        steps = self._steps()
        skip = commands.steps_to_skip_on_failure(steps[1], steps[2:], code=1)
        self.assertEqual(skip, [])

    def test_none_step_safe(self):
        self.assertEqual(commands.steps_to_skip_on_failure(None, [], code=1), [])


if __name__ == "__main__":
    unittest.main()
