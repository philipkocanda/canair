"""Shared path roots and lazily-resolved constants.

Path roots (``PACKAGE_DIR``, ``SCRIPT_DIR``, ``BUNDLED_PROFILES_DIR``,
``SCHEMA_DIR``) are static. Vehicle-data paths (``PIDS_DIR``, ``ECUS_FILE``,
``CAPTURES_DIR``) and WiCAN settings (``WICAN_ADDRESSES``, ``DEFAULT_WICAN``)
are resolved lazily via :mod:`canlib.profile` / :mod:`canlib.config` so the
active vehicle profile (``--profile`` / ``CANAIR_PROFILE``) is honored at
access time. Prefer resolving ``canlib.profile.active()`` directly in new code.
"""

from pathlib import Path

PACKAGE_DIR = Path(__file__).parent  # canlib/
SCRIPT_DIR = PACKAGE_DIR.parent  # repo root
BUNDLED_PROFILES_DIR = SCRIPT_DIR / "profiles"  # profiles shipped with the repo
SCHEMA_DIR = PACKAGE_DIR / "schema"  # tool-owned YAML/JSON schemas

# Legacy repo-local WiCAN config (deprecated in favor of ~/.config/canair/config.yaml)
CONFIG_FILE = SCRIPT_DIR / "config.yaml"

_LAZY = {"PIDS_DIR", "ECUS_FILE", "CAPTURES_DIR", "WICAN_ADDRESSES", "DEFAULT_WICAN"}


def __getattr__(name: str):
    """Resolve profile/config-dependent constants lazily (PEP 562)."""
    if name in ("PIDS_DIR", "ECUS_FILE", "CAPTURES_DIR"):
        from .profile import active

        prof = active()
        return {
            "PIDS_DIR": prof.pids_dir,
            "ECUS_FILE": prof.ecus_file,
            "CAPTURES_DIR": prof.captures_dir,
        }[name]
    if name in ("WICAN_ADDRESSES", "DEFAULT_WICAN"):
        from .config import wican_settings

        addresses, default = wican_settings()
        return addresses if name == "WICAN_ADDRESSES" else default
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
