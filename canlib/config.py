"""User/host configuration (XDG-aware) for canair.

Merges an optional legacy repo-local ``config.yaml`` (deprecated) with the
user config at ``$XDG_CONFIG_HOME/canair/config.yaml`` (default
``~/.config/canair/config.yaml``). The user config wins on conflicts.

Recognized keys:
  default_profile:  name of the vehicle profile to use when none is given
  profiles_dir:     extra directory to search for profiles
  wican_addresses:  mapping of alias -> IP/host for the --wican flag
  default_wican:    default --wican alias
  wican_model:      "pro" or "classic" — the WiCAN hardware model. AutoPID
                    profile features (canair wican device sync) and the
                    wican-ws ELM327 terminal are WiCAN Pro-only. Defaults to
                    "pro".
  transport:        transport-selection block (type/host/port/bitrate); see
                    canlib.transport.config
  check_for_updates: set to false to disable the automatic once-a-day check for
                    a newer released version (also disabled by the
                    CANAIR_NO_UPDATE_CHECK env var). See canlib.update_check.

View and edit config from the CLI with ``canair config`` (show/get/set/unset/
edit/path).
"""

from __future__ import annotations

import math
import os
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

from . import yaml_io
from .constants import CONFIG_FILE

# Fallback WiCAN address when nothing is configured (WiCAN AP mode).
_DEFAULT_ADDRESSES = {"ap": "192.168.80.1"}
_DEFAULT_WICAN_KEY = "ap"

# Auto-fallback defaults (the `transport:` block, keys fallback / connect_timeout
# / fallback_order). Fallback is on by default; the probe timeout is short so a
# dead device is skipped quickly (the full connect uses the normal, longer path).
_DEFAULT_FALLBACK = True
_DEFAULT_CONNECT_TIMEOUT = 2.0
# Bounded reconnect window (seconds) for a mid-session monitor drop when the user
# did NOT pass --wait. --wait retries forever instead; see wait_for_reachable.
_DEFAULT_RECONNECT_MAX_WAIT = 6.0

# WiCAN hardware model. AutoPID vehicle profiles and the wican-ws ELM327
# terminal are Pro-only; the classic (non-Pro) WiCAN only supports raw SLCAN.
WICAN_MODELS = ("pro", "classic")
_DEFAULT_WICAN_MODEL = "pro"


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
    an int, ``transport.connect_timeout 2.0`` stores a float, and
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
            return yaml_io.safe_load(f) or {}
    return {}


@lru_cache(maxsize=1)
def load_config() -> dict:
    """Load merged configuration (legacy repo config < user config)."""
    data: dict = {}
    data.update(_read_yaml(CONFIG_FILE))  # legacy repo-local (lower precedence)
    data.update(_read_yaml(user_config_file()))  # user config wins
    return data


def wican_settings() -> tuple[dict[str, str], str]:
    """Return (addresses, default_alias) from config or built-in fallbacks.

    Back-compat shim over :func:`wican_devices`: flattens the device map to
    ``{alias: host}`` for callers that only need the host per alias.
    """
    devices, default = wican_devices()
    return {alias: dev.host for alias, dev in devices.items()}, default


@dataclass(frozen=True)
class DeviceEntry:
    """A configured WiCAN/gateway device.

    ``host`` is always set (an IP or hostname). The optional per-device
    ``transport`` / ``port`` / ``bitrate`` override the global ``transport:``
    block for this device (see :func:`resolve_transport`), so a user with
    several devices can bind e.g. a home LAN device to ``slcan-tcp`` and a
    cellular/VPN device to ``wican-ws``.
    """

    host: str
    transport: str | None = None
    port: int | None = None
    bitrate: int | None = None


def _coerce_int(value) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _device_from(value) -> DeviceEntry | None:
    """Build a :class:`DeviceEntry` from a config value (string host or mapping)."""
    if isinstance(value, str):
        return DeviceEntry(host=value)
    if isinstance(value, dict):
        host = value.get("host")
        if not host:
            return None
        transport = value.get("transport")
        return DeviceEntry(
            host=str(host),
            transport=str(transport) if transport else None,
            port=_coerce_int(value.get("port")),
            bitrate=_coerce_int(value.get("bitrate")),
        )
    return None


def wican_devices() -> tuple[dict[str, DeviceEntry], str]:
    """Return (devices, default_alias) — the resolved device namespace.

    Precedence: a ``devices:`` block (rich per-device config) is authoritative
    and, when present, ``wican_addresses`` is ignored entirely. Otherwise the
    legacy flat ``wican_addresses`` map (each ``alias: ip`` a host-only device)
    is used, falling back to the built-in AP-mode address when neither is set.
    """
    cfg = load_config()
    default = cfg.get("default_wican", _DEFAULT_WICAN_KEY)

    devices: dict[str, DeviceEntry] = {}
    raw_devices = cfg.get("devices")
    if isinstance(raw_devices, dict) and raw_devices:
        for alias, value in raw_devices.items():
            dev = _device_from(value)
            if dev is not None:
                devices[str(alias)] = dev
        if devices:
            return devices, default

    raw_addrs = cfg.get("wican_addresses") or _DEFAULT_ADDRESSES
    return {str(k): DeviceEntry(host=str(v)) for k, v in raw_addrs.items()}, default


def fallback_settings() -> tuple[bool, float, list[str] | None]:
    """Return (enabled, connect_timeout, order) for cross-device auto-fallback.

    Read from the ``transport:`` block (keys ``fallback`` / ``connect_timeout``
    / ``fallback_order``). Fallback is on by default with a short probe timeout;
    ``order`` is an optional explicit list of device aliases to try.
    """
    raw_block = load_config().get("transport")
    block = raw_block if isinstance(raw_block, dict) else {}

    enabled = block.get("fallback", _DEFAULT_FALLBACK)
    enabled = bool(enabled) if not isinstance(enabled, str) else enabled.strip().lower() == "true"

    try:
        timeout = float(block.get("connect_timeout", _DEFAULT_CONNECT_TIMEOUT))
        if not math.isfinite(timeout) or timeout <= 0:
            timeout = _DEFAULT_CONNECT_TIMEOUT
    except (TypeError, ValueError):
        timeout = _DEFAULT_CONNECT_TIMEOUT

    order = block.get("fallback_order")
    if isinstance(order, str):
        order = [p.strip() for p in order.split(",") if p.strip()]
    elif isinstance(order, list):
        order = [str(x).strip() for x in order if str(x).strip()]
    else:
        order = None
    return enabled, timeout, order


def reconnect_max_wait() -> float:
    """Bounded reconnect window (seconds) for a mid-session monitor drop.

    Read from ``transport.reconnect_max_wait`` (default 6.0). This bounds the
    automatic reconnect attempt when the user did *not* pass ``--wait``; with
    ``--wait`` the monitor retries indefinitely instead. A non-positive or
    non-finite value falls back to the default.
    """
    raw_block = load_config().get("transport")
    block = raw_block if isinstance(raw_block, dict) else {}
    try:
        value = float(block.get("reconnect_max_wait", _DEFAULT_RECONNECT_MAX_WAIT))
        if not math.isfinite(value) or value <= 0:
            return _DEFAULT_RECONNECT_MAX_WAIT
    except (TypeError, ValueError):
        return _DEFAULT_RECONNECT_MAX_WAIT
    return value


def wican_model() -> str:
    """Return the configured WiCAN hardware model ("pro" or "classic").

    Defaults to "pro" so existing setups keep working without any config
    change. Unknown values fall back to "pro" as well (permissive).
    """
    value = str(load_config().get("wican_model", _DEFAULT_WICAN_MODEL)).strip().lower()
    return value if value in WICAN_MODELS else _DEFAULT_WICAN_MODEL


def is_wican_pro() -> bool:
    """True when the configured WiCAN model supports Pro-only features."""
    return wican_model() == "pro"
