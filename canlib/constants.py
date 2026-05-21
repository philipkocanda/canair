"""Shared constants and paths."""

from pathlib import Path

import yaml

SCRIPT_DIR = Path(__file__).parent.parent

PIDS_DIR = SCRIPT_DIR / "pids"
ECUS_FILE = SCRIPT_DIR / "ecus.yaml"

# ── WiCAN configuration (loaded from config.yaml) ─────────────────────────

CONFIG_FILE = SCRIPT_DIR / "config.yaml"

# Fallback defaults when no config.yaml exists (WiCAN AP mode address).
_DEFAULT_ADDRESSES = {"ap": "192.168.80.1"}
_DEFAULT_WICAN_KEY = "ap"


def _load_config() -> dict:
    """Load config.yaml if it exists, otherwise return empty dict."""
    if CONFIG_FILE.exists():
        with open(CONFIG_FILE) as f:
            return yaml.safe_load(f) or {}
    return {}


def _init_wican_settings() -> tuple[dict[str, str], str]:
    """Return (addresses_dict, default_key) from config or fallbacks."""
    cfg = _load_config()
    addresses = cfg.get("wican_addresses", _DEFAULT_ADDRESSES)
    default = cfg.get("default_wican", _DEFAULT_WICAN_KEY)
    # Ensure all values are plain strings (no http:// prefix — callers add scheme)
    addresses = {k: str(v) for k, v in addresses.items()}
    return addresses, default


WICAN_ADDRESSES, DEFAULT_WICAN = _init_wican_settings()
