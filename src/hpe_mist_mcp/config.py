"""Configuration loading and saving for the HPE Networking Assistant.

Resolution order (highest priority first):

1. Environment variables (``MIST_API_TOKEN``, ``MIST_REGION``, ``MIST_ORG_ID``)
   — this is how the Claude Desktop extension injects user_config values.
2. A JSON config file (default: ``~/.hpe-networking-assistant/config.json``).

The API token is treated as a secret. When written by the setup wizard, the
config file is created with owner-only permissions (0600) where the OS
supports it. In the packaged Claude Desktop extension the token is stored in
the OS keychain and supplied via environment variable, so it need not be
written to disk at all.
"""

from __future__ import annotations

import json
import os
import stat
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Optional

from .regions import normalize_region

ENV_TOKEN = "MIST_API_TOKEN"
ENV_REGION = "MIST_REGION"
ENV_ORG = "MIST_ORG_ID"
ENV_WRITE = "MIST_WRITE_ENABLED"

_TRUE = {"1", "true", "yes", "on", "enabled"}


def _as_bool(value) -> bool:
    if isinstance(value, bool):
        return value
    if value is None:
        return False
    return str(value).strip().lower() in _TRUE

DEFAULT_CONFIG_DIR = Path.home() / ".hpe-networking-assistant"
DEFAULT_CONFIG_PATH = DEFAULT_CONFIG_DIR / "config.json"


@dataclass
class Config:
    """Runtime configuration for the MCP server and tooling."""

    token: Optional[str] = None
    region: Optional[str] = None  # None => auto-detect from the token
    org_id: Optional[str] = None
    write_enabled: bool = False

    def require_token(self) -> str:
        if not self.token:
            raise ValueError(
                "No Mist API token configured. Set the MIST_API_TOKEN "
                "environment variable or run the setup wizard."
            )
        return self.token

    def redacted(self) -> dict:
        """Return a dict safe for logging (token masked)."""
        data = asdict(self)
        if data.get("token"):
            tok = data["token"]
            data["token"] = f"{tok[:4]}…{tok[-2:]}" if len(tok) > 6 else "set"
        return data


def config_path() -> Path:
    """Return the config file path, honoring HPE_ASSISTANT_CONFIG override."""
    override = os.environ.get("HPE_ASSISTANT_CONFIG")
    return Path(override) if override else DEFAULT_CONFIG_PATH


def load_config() -> Config:
    """Load configuration from environment and/or the config file."""
    cfg = Config()

    path = config_path()
    if path.exists():
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            cfg.token = data.get("token") or cfg.token
            cfg.region = data.get("region") or cfg.region
            cfg.org_id = data.get("org_id") or cfg.org_id
            if "write_enabled" in data:
                cfg.write_enabled = _as_bool(data.get("write_enabled"))
        except (json.JSONDecodeError, OSError):
            pass  # fall back to environment / defaults

    # Environment variables take precedence (used by the desktop extension).
    cfg.token = os.environ.get(ENV_TOKEN) or cfg.token
    cfg.org_id = os.environ.get(ENV_ORG) or cfg.org_id
    if os.environ.get(ENV_WRITE) is not None:
        cfg.write_enabled = _as_bool(os.environ.get(ENV_WRITE))

    # Region: env > saved file value > auto-detect (None). "auto" also means detect.
    region_val = os.environ.get(ENV_REGION) or cfg.region
    if region_val and str(region_val).strip().lower() != "auto":
        cfg.region = normalize_region(region_val)
    else:
        cfg.region = None
    return cfg


def save_config(cfg: Config, path: Optional[Path] = None) -> Path:
    """Persist configuration to disk with restrictive permissions."""
    target = path or config_path()
    target.parent.mkdir(parents=True, exist_ok=True)

    payload = {
        "token": cfg.token,
        "region": normalize_region(cfg.region),
        "org_id": cfg.org_id,
        "write_enabled": bool(cfg.write_enabled),
    }
    target.write_text(json.dumps(payload, indent=2), encoding="utf-8")

    # Best-effort lock-down of the file holding the token.
    try:
        target.chmod(stat.S_IRUSR | stat.S_IWUSR)
    except OSError:
        pass
    return target


def save_discovery(region: Optional[str] = None, org_id: Optional[str] = None,
                   path: Optional[Path] = None) -> Path:
    """Persist auto-discovered region/org without ever writing the API token.

    Used by the onboarding flow so the customer never has to re-enter (or even
    know) their region or organization id. The token stays in the OS keychain.
    """
    target = path or config_path()
    target.parent.mkdir(parents=True, exist_ok=True)

    data: dict = {}
    if target.exists():
        try:
            data = json.loads(target.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            data = {}

    if region:
        data["region"] = normalize_region(region)
    if org_id:
        data["org_id"] = org_id
    data.pop("token", None)  # never persist the secret here

    target.write_text(json.dumps(data, indent=2), encoding="utf-8")
    try:
        target.chmod(stat.S_IRUSR | stat.S_IWUSR)
    except OSError:
        pass
    return target
