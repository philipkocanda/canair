"""Shared constants and paths."""

from pathlib import Path

SCRIPT_DIR = Path(__file__).parent.parent

PIDS_DIR = SCRIPT_DIR / "pids"

WICAN_ADDRESSES = {
    "home": "10.0.2.86",
    "vpn": "192.168.3.2",
}
DEFAULT_WICAN = "home"

ECUS_FILE = SCRIPT_DIR / "ecus.yaml"
