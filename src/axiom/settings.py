"""Runtime settings read from the environment.

Paths default to the repository layout and can be overridden in containers.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]


def _path(name: str, default: Path) -> Path:
    value = os.environ.get(name)
    return Path(value) if value else default


def _keys(name: str) -> dict[str, str]:
    """Parse 'name:key,name2:key2' into {key: name}."""
    out: dict[str, str] = {}
    for item in os.environ.get(name, "").split(","):
        item = item.strip()
        if not item:
            continue
        principal, sep, key = item.partition(":")
        if not sep or not principal or not key:
            raise ValueError(f"{name} entries must look like 'name:key'")
        out[key] = principal
    return out


@dataclass(frozen=True)
class Settings:
    mode: str = "demo"
    config_dir: Path = REPO_ROOT / "config"
    data_dir: Path = REPO_ROOT / "data"
    prompts_dir: Path = REPO_ROOT / "prompts"
    database_url: str | None = None
    gkn_mcp_url: str | None = None
    regulus_mcp_url: str | None = None
    mcp_shared_secret: str | None = None
    otel_endpoint: str | None = None
    submitter_keys: dict[str, str] = field(default_factory=dict)
    reviewer_keys: dict[str, str] = field(default_factory=dict)
    auditor_keys: dict[str, str] = field(default_factory=dict)
    inprocess_worker: bool = False
    recordings_override: Path | None = None

    @classmethod
    def from_env(cls) -> Settings:
        return cls(
            mode=os.environ.get("AXIOM_MODE", "demo"),
            config_dir=_path("AXIOM_CONFIG_DIR", REPO_ROOT / "config"),
            data_dir=_path("AXIOM_DATA_DIR", REPO_ROOT / "data"),
            prompts_dir=_path("AXIOM_PROMPTS_DIR", REPO_ROOT / "prompts"),
            database_url=os.environ.get("DATABASE_URL") or None,
            gkn_mcp_url=os.environ.get("GKN_MCP_URL") or None,
            regulus_mcp_url=os.environ.get("REGULUS_MCP_URL") or None,
            mcp_shared_secret=os.environ.get("MCP_SHARED_SECRET") or None,
            otel_endpoint=os.environ.get("OTEL_EXPORTER_OTLP_ENDPOINT") or None,
            submitter_keys=_keys("SUBMITTER_API_KEYS"),
            reviewer_keys=_keys("REVIEWER_API_KEYS"),
            auditor_keys=_keys("AUDITOR_API_KEYS"),
            inprocess_worker=os.environ.get("AXIOM_INPROCESS_WORKER", "").lower()
            in {"1", "true", "yes"},
            recordings_override=_path("AXIOM_RECORDINGS_DIR", REPO_ROOT / "data" / "recorded")
            if os.environ.get("AXIOM_RECORDINGS_DIR")
            else None,
        )

    @property
    def reference_dirs(self) -> dict[str, Path]:
        """Read-only reference collections, as GKN mounts them in docker-compose.yml."""
        return {
            "credit_policy": self.data_dir / "synthetic" / "credit_policy",
            "lending_refs": self.data_dir / "lending_refs",
        }

    @property
    def packages_dir(self) -> Path:
        return self.data_dir / "synthetic" / "packages"

    @property
    def recordings_dir(self) -> Path:
        """Responses demo mode replays: live recordings by default, or AXIOM_RECORDINGS_DIR."""
        return self.recordings_override or self.data_dir / "recorded"
