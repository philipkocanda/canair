"""User/host configuration (XDG-aware) for canair.

Merges an optional legacy repo-local ``config.yaml`` (deprecated) with the
user config at ``$XDG_CONFIG_HOME/canair/config.yaml`` (default
``~/.config/canair/config.yaml``). The user config wins on conflicts.

Recognized keys:
  default_profile:  name of the vehicle profile to use when none is given
  profiles_dir:     extra directory to search for profiles
  wican_addresses:  mapping of alias -> IP/host for the --wican flag
  default_wican:    default --wican alias
"""

from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path

import yaml

from .constants import CONFIG_FILE

# Fallback WiCAN address when nothing is configured (WiCAN AP mode).
_DEFAULT_ADDRESSES = {"ap": "192.168.80.1"}
_DEFAULT_WICAN_KEY = "ap"


def config_dir() -> Path:
    """Return the canair config directory ($XDG_CONFIG_HOME/canair)."""
    base = os.environ.get("XDG_CONFIG_HOME") or (Path.home() / ".config")
    return Path(base) / "canair"


def user_config_file() -> Path:
    """Return the path to the user config file (may not exist)."""
    return config_dir() / "config.yaml"


def _read_yaml(path: Path) -> dict:
    if path.exists():
        with open(path) as f:
            return yaml.safe_load(f) or {}
    return {}


@lru_cache(maxsize=1)
def load_config() -> dict:
    """Load merged configuration (legacy repo config < user config)."""
    data: dict = {}
    data.update(_read_yaml(CONFIG_FILE))  # legacy repo-local (lower precedence)
    data.update(_read_yaml(user_config_file()))  # user config wins
    return data


def wican_settings() -> tuple[dict[str, str], str]:
    """Return (addresses, default_alias) from config or built-in fallbacks."""
    cfg = load_config()
    addresses = cfg.get("wican_addresses") or _DEFAULT_ADDRESSES
    addresses = {k: str(v) for k, v in addresses.items()}
    default = cfg.get("default_wican", _DEFAULT_WICAN_KEY)
    return addresses, default
