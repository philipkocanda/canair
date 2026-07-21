"""User/host configuration (XDG-aware) for canair.

Merges an optional legacy repo-local ``config.yaml`` (deprecated) with the
user config at ``$XDG_CONFIG_HOME/canair/config.yaml`` (default
``~/.config/canair/config.yaml``). The user config wins on conflicts.

Recognized keys:
  default_profile:  name of the vehicle profile to use when none is given
  profiles_dir:     extra directory to search for profiles
  wican_addresses:  mapping of alias -> IP/host for the --wican flag
  default_wican:    default --wican alias
  transport:        transport-selection block (type/host/port/bitrate); see
                    canlib.transport.config

View and edit config from the CLI with ``canair config`` (show/get/set/unset/
edit/path).
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


def user_profiles_dir() -> Path:
    """Return the user profiles directory ($XDG_CONFIG_HOME/canair/profiles)."""
    return config_dir() / "profiles"


_STARTER_CONFIG = """\
# canair configuration — see `canair --help` and config.example.yaml in the repo.
# This file was created automatically; edit it to taste. All keys are optional.

# Vehicle profile to use when none is given (--profile / CANAIR_PROFILE override).
# Auto-selected when exactly one profile is discovered, so this is optional.
# default_profile: ioniq-2017

# Extra directory to search for vehicle profiles (in addition to this dir's
# profiles/ subfolder and the repo-bundled profiles/).
# profiles_dir: ~/vehicles

# WiCAN device addresses for the --wican flag (alias -> IP/host).
# wican_addresses:
#   ap: "192.168.80.1"    # WiCAN AP mode (factory default)
#   home: "192.168.1.100"
# default_wican: ap
"""


def ensure_config_dir(seed_config: bool = True) -> Path:
    """Create ``~/.config/canair`` (and ``profiles/``) if missing.

    When ``seed_config`` is True and no config file exists yet, a commented
    starter ``config.yaml`` is written so users have a discoverable place to
    configure the tool without any manual setup. Best-effort: filesystem errors
    are swallowed so a read-only HOME never breaks the CLI.
    """
    cfg_dir = config_dir()
    try:
        cfg_dir.mkdir(parents=True, exist_ok=True)
        user_profiles_dir().mkdir(parents=True, exist_ok=True)
        cfg_file = user_config_file()
        if seed_config and not cfg_file.exists():
            cfg_file.write_text(_STARTER_CONFIG)
    except OSError:
        pass
    return cfg_dir


def coerce_scalar(value: str):
    """Coerce a CLI string into a bool/int/None where unambiguous, else str.

    Used by ``canair config set`` so that e.g. ``transport.port 35000`` stores
    an int and ``true``/``false`` store bools. IPs/hostnames stay strings (they
    never parse as int). Pass through :func:`set_config_key` with ``--string``
    to bypass this.
    """
    low = value.strip().lower()
    if low in ("true", "false"):
        return low == "true"
    if low in ("null", "none", "~"):
        return None
    try:
        return int(value)
    except ValueError:
        return value


def set_config_key(key: str, value) -> Path:
    """Set a (possibly dotted) ``key`` in the user config, preserving layout.

    ``key`` may be a dotted path into nested mappings (e.g. ``transport.port``
    or ``wican_addresses.home``); intermediate mappings are created as needed.
    Comments and formatting in an existing config survive the edit. Returns the
    config file path and invalidates the cached config.
    """
    from ruamel.yaml.comments import CommentedMap

    from .yaml_rt import dump, round_trip_yaml

    ensure_config_dir()
    path = user_config_file()
    text = path.read_text() if path.exists() else ""
    data = round_trip_yaml().load(text) if text.strip() else None
    parts = key.split(".")

    if not isinstance(data, dict):
        # Empty or all-comment file: append fresh YAML so the (helpful) comment
        # block seeded by ensure_config_dir() survives the first write.
        node = root = CommentedMap()
        for part in parts[:-1]:
            child = CommentedMap()
            node[part] = child
            node = child
        node[parts[-1]] = value
        from io import StringIO

        buf = StringIO()
        dump(root, buf)
        if text and not text.endswith("\n"):
            text += "\n"
        text += buf.getvalue()
        path.write_text(text)
    else:
        node = data
        for part in parts[:-1]:
            if not isinstance(node.get(part), dict):
                node[part] = CommentedMap()
            node = node[part]
        node[parts[-1]] = value
        with open(path, "w") as f:
            dump(data, f)

    load_config.cache_clear()
    return path


def unset_config_key(key: str) -> tuple[Path, bool]:
    """Remove a (possibly dotted) ``key`` from the user config.

    Returns ``(path, removed)`` where ``removed`` is False if the key was
    absent. Comments and formatting are preserved.
    """
    from .yaml_rt import dump, round_trip_yaml

    path = user_config_file()
    if not path.exists():
        return path, False
    text = path.read_text()
    data = round_trip_yaml().load(text) if text.strip() else None
    if not isinstance(data, dict):
        return path, False

    parts = key.split(".")
    node = data
    for part in parts[:-1]:
        nxt = node.get(part) if isinstance(node, dict) else None
        if not isinstance(nxt, dict):
            return path, False
        node = nxt
    if parts[-1] not in node:
        return path, False

    del node[parts[-1]]
    with open(path, "w") as f:
        dump(data, f)
    load_config.cache_clear()
    return path, True


def get_config_key(key: str):
    """Return the merged-config value at a dotted ``key`` (None if absent)."""
    node = load_config()
    for part in key.split("."):
        if isinstance(node, dict) and part in node:
            node = node[part]
        else:
            return None
    return node


def set_config_value(key: str, value: str) -> Path:
    """Set a top-level scalar ``key: value`` in the user config file.

    Thin wrapper over :func:`set_config_key` kept for back-compat; stores the
    value verbatim (no coercion). Returns the config file path.
    """
    return set_config_key(key, value)


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
