"""The user config file: its paths, first-run seeding, merged load, get, set and unset."""

from __future__ import annotations

import math
from functools import lru_cache
from pathlib import Path

from canair.core import yaml_io
from canair.core.paths import CONFIG_FILE, config_dir


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
# default_profile: hyundai-ioniq-ae-ev

# Extra directory to search for vehicle profiles (in addition to this dir's
# profiles/ subfolder and the repo-bundled profiles/).
# profiles_dir: ~/vehicles

# WiCAN/gateway devices for the --wican flag (alias -> host, optional per-device
# transport/port/bitrate).
# devices:
#   ap:
#     host: "192.168.80.1"    # WiCAN AP mode (factory default)
#   home:
#     host: "192.168.1.100"
#     transport: slcan-tcp    # optional: slcan-tcp | wican-ws (per device)
# default_wican: ap
#
# When the selected device is unreachable, canair auto-falls-back to the others
# (transport.fallback, default true; --no-fallback to disable per-command).

# WiCAN hardware model: "pro" (default) or "classic" (non-Pro). The classic
# WiCAN has no AutoPID profile support and no ELM327 WebSocket terminal, so
# `canair wican autopid upload/download/diff`, `canair wican mode set`, and the
# wican-ws transport are refused for it. Raw slcan-tcp works on both.
# wican_model: pro

# Automatic update check: canair checks GitHub once a day (in the background,
# never blocking) for a newer released version and prints a one-line notice.
# Set to false to disable it (the CANAIR_NO_UPDATE_CHECK env var does the same).
# check_for_updates: true
"""


def ensure_config_dir(seed_config: bool = True) -> bool:
    """Create ``~/.config/canair`` (and ``profiles/``) if missing.

    When ``seed_config`` is True and no config file exists yet, a commented
    starter ``config.yaml`` is written so users have a discoverable place to
    configure the tool without any manual setup. Best-effort: filesystem errors
    are swallowed so a read-only HOME never breaks the CLI.

    Returns True when it just seeded a fresh config file (a genuine first run),
    False otherwise.
    """
    cfg_dir = config_dir()
    seeded = False
    try:
        cfg_dir.mkdir(parents=True, exist_ok=True)
        user_profiles_dir().mkdir(parents=True, exist_ok=True)
        cfg_file = user_config_file()
        if seed_config and not cfg_file.exists():
            cfg_file.write_text(_STARTER_CONFIG)
            seeded = True
    except OSError:
        pass
    return seeded


def coerce_scalar(value: str):
    """Coerce a CLI string into a bool/int/float/None where unambiguous, else str.

    Used by ``canair config set`` so that e.g. ``transport.port 35000`` stores
    an int, ``transport.connect_timeout 5.0`` stores a float, and
    ``true``/``false`` store bools. IPs/hostnames stay strings (they never parse
    as a number); non-finite floats (``inf``/``nan``) are kept as strings. Pass
    through :func:`set_config_key` with ``--string`` to bypass this.
    """
    low = value.strip().lower()
    if low in ("true", "false"):
        return low == "true"
    if low in ("null", "none", "~"):
        return None
    try:
        return int(value)
    except ValueError:
        pass
    try:
        f = float(value)
    except ValueError:
        return value
    # Reject inf/nan (float() accepts them) so they stay strings, not silent
    # non-finite config values.
    return f if math.isfinite(f) else value


def set_config_key(key: str, value) -> Path:
    """Set a (possibly dotted) ``key`` in the user config, preserving layout.

    ``key`` may be a dotted path into nested mappings (e.g. ``transport.port``
    or ``wican_addresses.home``); intermediate mappings are created as needed.
    Comments and formatting in an existing config survive the edit. Returns the
    config file path and invalidates the cached config.
    """
    from ruamel.yaml.comments import CommentedMap

    from canair.core.yaml_rt import dump, round_trip_yaml

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
    from canair.core.yaml_rt import dump, round_trip_yaml

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
            return yaml_io.safe_load(f) or {}
    return {}


@lru_cache(maxsize=1)
def load_config() -> dict:
    """Load merged configuration (legacy repo config < user config)."""
    data: dict = {}
    data.update(_read_yaml(CONFIG_FILE))  # legacy repo-local (lower precedence)
    data.update(_read_yaml(user_config_file()))  # user config wins
    return data
