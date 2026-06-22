"""Data model — pure dataclasses, no Qt, no I/O.

A profile (``Job``) chooses an engine ("rsync" or "restic"). rsync options and
restic config coexist on every job; only the active engine's fields are used.
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass, asdict, field
from typing import Any, Dict, List


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
        for k in ("bucket", "region", "endpoint", "prefix", "repository",
                  "password", "access_key_id", "secret_access_key"):
            obj.__dict__[k] = str(d.get(k, ""))
        obj.tags = [str(t) for t in d.get("tags", [])]
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
class Job:
    name: str
    engine: str            # "rsync" | "restic"
    id: str
    sources: List[str]
    destination: str       # rsync destination (unused by restic)
    excludes: List[str]
    rsync: RsyncOptions
    restic: ResticConfig

    @staticmethod
    def from_dict(d: Dict[str, Any]) -> "Job":
        # Backward compat: v1 stored rsync flags under "options" and had no
        # "engine"/"id"/"restic" keys.
        rsync_d = d.get("rsync", d.get("options", {}))
        return Job(
            name=d.get("name", "Job"),
            engine=d.get("engine", "rsync"),
            id=str(d.get("id") or uuid.uuid4().hex),
            sources=list(d.get("sources", [])),
            destination=str(d.get("destination", "")),
            excludes=list(d.get("excludes", [])),
            rsync=RsyncOptions.from_dict(rsync_d),
            restic=ResticConfig.from_dict(d.get("restic", {})),
        )

    def to_dict(self) -> Dict[str, Any]:
        return {
            "name": self.name,
            "engine": self.engine,
            "id": self.id,
            "sources": self.sources,
            "destination": self.destination,
            "excludes": self.excludes,
            "rsync": asdict(self.rsync),
            "restic": self.restic.to_dict(),
        }


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
