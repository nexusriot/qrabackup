"""Data model — pure dataclasses, no Qt, no I/O.

A profile (``Job``) chooses an engine ("rsync" or "restic"). rsync options and
restic config coexist on every job; only the active engine's fields are used.
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass, asdict, field
from typing import Any, Dict, List, Optional


def _as_dict(v: Any) -> Dict[str, Any]:
    """Hand-edited / corrupted settings may hold null or a wrong type where an
    object is expected; treat that as empty rather than crashing on .get()."""
    return v if isinstance(v, dict) else {}


def _as_str_list(v: Any) -> List[str]:
    """A list of strings, or [] — never ``list("abc") == ['a', 'b', 'c']``."""
    if not isinstance(v, list):
        return []
    return [str(x) for x in v if isinstance(x, (str, int, float))]


ENGINES = ("rsync", "restic")


@dataclass
class RsyncOptions:
    archive: bool = True
    verbose: bool = False
    compress: bool = False
    delete: bool = False
    preserve: bool = False  # perms/owner/group/devices/times
    dry_run: bool = False
    progress: bool = True

    @staticmethod
    def from_dict(d: Dict[str, Any]) -> "RsyncOptions":
        obj = RsyncOptions()
        d = _as_dict(d)
        for k in asdict(obj).keys():
            if k in d:
                setattr(obj, k, bool(d[k]))
        return obj


@dataclass
class ResticRetention:
    enabled: bool = False  # run `forget --prune` after a successful backup
    keep_last: int = 0
    keep_daily: int = 7
    keep_weekly: int = 4
    keep_monthly: int = 6
    keep_yearly: int = 0
    prune: bool = True

    @staticmethod
    def from_dict(d: Dict[str, Any]) -> "ResticRetention":
        obj = ResticRetention()
        d = _as_dict(d)
        obj.enabled = bool(d.get("enabled", obj.enabled))
        obj.prune = bool(d.get("prune", obj.prune))
        for k in ("keep_last", "keep_daily", "keep_weekly", "keep_monthly", "keep_yearly"):
            try:
                setattr(obj, k, int(d.get(k, getattr(obj, k))))
            except (TypeError, ValueError):
                pass
        return obj


@dataclass
class ResticConfig:
    bucket: str = ""
    region: str = ""
    endpoint: str = ""        # blank = AWS; e.g. https://minio.local:9000
    prefix: str = ""          # path within the bucket
    repository: str = ""      # advanced: raw "s3:..." override
    password: str = ""        # RESTIC_PASSWORD (plaintext, masked in UI)
    access_key_id: str = ""   # AWS_ACCESS_KEY_ID
    secret_access_key: str = ""  # AWS_SECRET_ACCESS_KEY (masked)
    tags: List[str] = field(default_factory=list)
    retention: ResticRetention = field(default_factory=ResticRetention)

    @staticmethod
    def from_dict(d: Dict[str, Any]) -> "ResticConfig":
        obj = ResticConfig()
        d = _as_dict(d)
        for k in ("bucket", "region", "endpoint", "prefix", "repository",
                  "password", "access_key_id", "secret_access_key"):
            v = d.get(k)
            obj.__dict__[k] = "" if v is None else str(v)
        obj.tags = _as_str_list(d.get("tags"))
        obj.retention = ResticRetention.from_dict(d.get("retention", {}))
        return obj

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    def build_repository(self) -> str:
        """Compose the restic repository string from the fields, or return the
        raw override if set. Empty string means 'not enough info yet'."""
        if self.repository.strip():
            return self.repository.strip()
        bucket = self.bucket.strip().strip("/")
        if not bucket:
            return ""
        endpoint = self.endpoint.strip().rstrip("/")
        region = self.region.strip()
        if endpoint:
            base = f"s3:{endpoint}/{bucket}"
        elif region:
            base = f"s3:s3.{region}.amazonaws.com/{bucket}"
        else:
            base = f"s3:s3.amazonaws.com/{bucket}"
        prefix = self.prefix.strip().strip("/")
        if prefix:
            base = f"{base}/{prefix}"
        return base

    def build_env(self) -> Dict[str, str]:
        env: Dict[str, str] = {}
        if self.password:
            env["RESTIC_PASSWORD"] = self.password
        if self.access_key_id:
            env["AWS_ACCESS_KEY_ID"] = self.access_key_id
        if self.secret_access_key:
            env["AWS_SECRET_ACCESS_KEY"] = self.secret_access_key
        if self.region.strip():
            env["AWS_DEFAULT_REGION"] = self.region.strip()
        return env


@dataclass
class LastRun:
    """Outcome of the profile's most recent backup step (not manual actions
    like init/check/restore)."""
    when: str = ""          # local time, ISO "YYYY-MM-DDTHH:MM:SS"
    exit_code: int = 0
    duration_s: float = 0.0

    @staticmethod
    def from_dict(d: Dict[str, Any]) -> "LastRun":
        obj = LastRun()
        d = _as_dict(d)
        obj.when = str(d.get("when", ""))
        try:
            obj.exit_code = int(d.get("exit_code", 0))
        except (TypeError, ValueError):
            pass
        try:
            obj.duration_s = float(d.get("duration_s", 0.0))
        except (TypeError, ValueError):
            pass
        return obj


@dataclass
class Job:
    name: str
    engine: str            # "rsync" | "restic"
    id: str
    sources: List[str]
    destination: str       # rsync destination (unused by restic)
    excludes: List[str]
    rsync: RsyncOptions
    restic: ResticConfig
    last_run: Optional[LastRun] = None

    @staticmethod
    def from_dict(d: Dict[str, Any]) -> "Job":
        # Backward compat: v1 stored rsync flags under "options" and had no
        # "engine"/"id"/"restic" keys.
        d = _as_dict(d)
        rsync_d = d.get("rsync", d.get("options", {}))
        lr = d.get("last_run")
        engine = str(d.get("engine") or "rsync").strip().lower()
        dest = d.get("destination")
        return Job(
            name=str(d.get("name") or "Job"),
            engine=engine if engine in ENGINES else "rsync",
            id=str(d.get("id") or uuid.uuid4().hex),
            sources=_as_str_list(d.get("sources")),
            destination="" if dest is None else str(dest),
            excludes=_as_str_list(d.get("excludes")),
            rsync=RsyncOptions.from_dict(rsync_d),
            restic=ResticConfig.from_dict(d.get("restic", {})),
            last_run=LastRun.from_dict(lr) if isinstance(lr, dict) else None,
        )

    def to_dict(self) -> Dict[str, Any]:
        d = {
            "name": self.name,
            "engine": self.engine,
            "id": self.id,
            "sources": self.sources,
            "destination": self.destination,
            "excludes": self.excludes,
            "rsync": asdict(self.rsync),
            "restic": self.restic.to_dict(),
        }
        if self.last_run is not None:
            d["last_run"] = asdict(self.last_run)
        return d


def new_job(name: str, engine: str = "rsync") -> Job:
    return Job(
        name=name,
        engine=engine,
        id=uuid.uuid4().hex,
        sources=[],
        destination="",
        excludes=[],
        rsync=RsyncOptions(),
        restic=ResticConfig(),
    )
