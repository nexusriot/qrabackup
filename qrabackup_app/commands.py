"""Command construction and run planning — pure logic, no Qt.

These build the exact argv (and environment) for rsync/restic, and expand a
profile into an ordered list of ``RunStep`` the runner executes. Keeping this
out of the UI makes it directly unit-testable.
"""
from __future__ import annotations

import shlex
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Tuple

from .model import Job
from .parsers import (
    PlainProgressParser,
    ResticJsonProgressParser,
    RsyncProgressParser,
)

Env = Dict[str, str]
ArgsEnv = Tuple[List[str], Env]


@dataclass
class RunStep:
    """One subprocess in a run. Steps sharing a ``group`` belong to the same
    profile; if a step with ``abort_group_on_fail`` fails, the remaining steps
    in that group are skipped (but other groups still run)."""
    group: int
    label: str
    program: str
    args: List[str]
    env: Optional[Env]
    parser: Any
    abort_group_on_fail: bool = False


def rsync_args(job: Job) -> List[str]:
    opt = job.rsync
    args: List[str] = []
    if opt.archive:
        args.append("-a")  # -a implies -r, so directories recurse
    else:
        # Without archive there is no recursion flag, which would silently back
        # up only top-level files. Keep recursion on regardless.
        args.append("-r")
    if opt.verbose:
        args.append("-v")
    if opt.compress:
        args.append("-z")
    if opt.preserve:
        args += ["-p", "-o", "-g", "-D", "-t"]
    if opt.delete:
        args.append("--delete")
    if opt.dry_run:
        args.append("--dry-run")
    if opt.progress:
        args.append("--info=progress2")
    for pat in job.excludes:
        args += ["--exclude", pat]
    if not job.sources:
        raise ValueError("Profile has no sources")
    if not job.destination:
        raise ValueError("Profile has no destination")
    # A source WITHOUT a trailing slash is copied as a named subdirectory inside
    # the destination, preserving folder structure and preventing multiple
    # sources from merging (and clobbering with --delete) at the destination
    # root. We respect whatever the user entered.
    args += list(job.sources)
    args.append(job.destination)
    return args


def restic_base_args(job: Job) -> ArgsEnv:
    """Return (['-r', repo], env) or raise ValueError with a clear message."""
    repo = job.restic.build_repository()
    if not repo:
        raise ValueError("restic profile has no repository (set a bucket, or a raw repository)")
    if not job.restic.password:
        raise ValueError("restic profile has no repository password")
    return ["-r", repo], job.restic.build_env()


def restic_backup_args(job: Job) -> ArgsEnv:
    base, env = restic_base_args(job)
    if not job.sources:
        raise ValueError("Profile has no sources")
    args = base + ["backup", "--json"]
    for t in job.restic.tags:
        args += ["--tag", t]
    for pat in job.excludes:
        args += ["--exclude", pat]
    args += list(job.sources)
    return args, env


def restic_forget_args(job: Job, *, prune: bool, dry_run: bool = False) -> ArgsEnv:
    """Build ``forget`` args from the profile's retention policy. Scoped to the
    profile's tags (so other backups sharing the repo aren't affected). Raises
    ValueError if no keep-* values are set (which would forget all snapshots)."""
    base, env = restic_base_args(job)
    r = job.restic.retention
    keep = [
        ("--keep-last", r.keep_last), ("--keep-daily", r.keep_daily),
        ("--keep-weekly", r.keep_weekly), ("--keep-monthly", r.keep_monthly),
        ("--keep-yearly", r.keep_yearly),
    ]
    args = base + ["forget"]
    added = False
    for flag, val in keep:
        if val and val > 0:
            args += [flag, str(val)]
            added = True
    if not added:
        raise ValueError("retention has no keep-* values set — refusing to forget all snapshots")
    if job.restic.tags:
        # comma-joined => snapshots having ALL of this profile's tags
        args += ["--tag", ",".join(job.restic.tags)]
    if dry_run:
        args.append("--dry-run")
    elif prune and r.prune:
        args.append("--prune")
    return args, env


def masked_env_str(env: Env) -> str:
    """Render an env dict for display with secrets masked."""
    parts = []
    for k, v in env.items():
        if k in ("RESTIC_PASSWORD", "AWS_SECRET_ACCESS_KEY"):
            shown = "***"
        elif k == "AWS_ACCESS_KEY_ID":
            shown = (v[:4] + "…") if len(v) > 4 else "***"
        else:
            shown = v
        parts.append(f"{k}={shown}")
    return " ".join(parts)


def preview_command(job: Job) -> str:
    """The command that ``Run Selected`` would execute, with secrets masked.
    Raises ValueError if the profile is incompletely configured."""
    if job.engine == "restic":
        args, env = restic_backup_args(job)
        env_str = masked_env_str(env)
        cmd = "restic " + " ".join(shlex.quote(a) for a in args)
        return (env_str + " " + cmd) if env_str else cmd
    args = rsync_args(job)
    return "rsync " + " ".join(shlex.quote(a) for a in args)


def build_run_steps(job: Job, group: int) -> List[RunStep]:
    """Expand a profile into its sequential run steps. A restic profile with
    retention enabled gets a forget+prune step after its backup. May raise
    ValueError (e.g. missing repo, or retention enabled but unset)."""
    if job.engine == "restic":
        bargs, env = restic_backup_args(job)
        steps = [RunStep(group, f"{job.name}: restic backup", "restic", bargs,
                         env, ResticJsonProgressParser(), abort_group_on_fail=True)]
        if job.restic.retention.enabled:
            # Raises if retention is enabled but unset — surfaced to the caller
            # so the profile is skipped/reported rather than nuking snapshots.
            fargs, _ = restic_forget_args(job, prune=True)
            steps.append(RunStep(group, f"{job.name}: forget + prune", "restic",
                                 fargs, env, PlainProgressParser()))
        return steps
    args = rsync_args(job)
    return [RunStep(group, f"{job.name}: rsync", "rsync", args, None, RsyncProgressParser())]


def steps_to_skip_on_failure(failed_step: Optional[RunStep],
                             remaining_steps: List[RunStep],
                             code: int) -> List[RunStep]:
    """Given a just-finished step's exit ``code`` and the queued remaining
    steps, return the follow-up steps that should be skipped: when an
    ``abort_group_on_fail`` step fails, every remaining step in its group."""
    if code != 0 and failed_step is not None and failed_step.abort_group_on_fail:
        return [s for s in remaining_steps if s.group == failed_step.group]
    return []
