"""The config keys ``canair config set`` knows, and checking a value against them."""

from __future__ import annotations

import sys

from canair.analysis.physical.grid_regions import GRID_REGIONS
from canair.config.file import ALLOW_RESET_KEY, ALLOW_UNSAFE_KEY
from canair.core.paths import CONFIG_DOCS_URL
from canair.render import messages
from canair.transport.config import VALID_TRANSPORTS

# Keys accepted by `config set`, offered for tab-completion / help. Dotted keys
# with a wildcard leaf (wican_addresses.<alias>) are documented, not enumerated.
KNOWN_KEYS = (
    "default_profile",
    "profile_settings.<profile>.variant",
    "profile_settings.<profile>.revisions.<ECU>",
    "profiles_dir",
    "default_wican",
    "devices.<alias>.host",
    "devices.<alias>.transport",
    "devices.<alias>.port",
    "devices.<alias>.bitrate",
    "wican_addresses.<alias>",
    "wican_model",
    "check_for_updates",
    "grid_region",
    "transport.type",
    "transport.host",
    "transport.port",
    "transport.bitrate",
    "transport.fallback",
    "transport.connect_timeout",
    "transport.fallback_order",
    "transport.reconnect_max_wait",
    "transport.ws_ping_interval",
    "transport.stale_cycles_before_reconnect",
    "transport.expected_responses",
    "transport.firmware_check",
    ALLOW_UNSAFE_KEY,
    ALLOW_RESET_KEY,
)


# Known keys whose value is an enum — validated up front so a typo gets a clear
# "valid: …" error instead of silently writing a value nothing will accept.
def _transport_types() -> tuple[str, ...]:
    return VALID_TRANSPORTS


def _grid_regions() -> tuple[str, ...]:
    return GRID_REGIONS


_WICAN_MODELS = ("pro", "classic")
_BOOLEANS = ("true", "false")
# The safety gates: booleans that `config set` enables only after typed consent (edit.py).
SAFETY_GATE_KEYS = (ALLOW_UNSAFE_KEY, ALLOW_RESET_KEY)


# Keys whose value is a list; `config set` splits a comma-separated value.
LIST_KEYS = ("transport.fallback_order",)


def _keys_block() -> str:
    """Aligned 'known keys' list with valid values for enum keys."""
    width = max(len(k) for k in KNOWN_KEYS)
    lines = []
    for k in KNOWN_KEYS:
        vals = enum_values(k)
        suffix = f"  valid: {', '.join(vals)}" if vals else ""
        lines.append(f"  {k:<{width}}{suffix}".rstrip())
    return "\n".join(lines)


def set_description() -> str:
    return (
        "Set a config value:  canair config set KEY VALUE\n"
        "Dotted keys create nested mappings (e.g. transport.type).\n\n"
        "known keys:\n" + _keys_block() + "\n\n"
        "Every key is documented in `canair config example` "
        "(config.example.yaml) and " + CONFIG_DOCS_URL
    )


SET_EPILOG = f"""\
examples:
  canair config set default_wican home
  canair config set devices.home.host 10.0.2.86
  canair config set devices.home.transport slcan-tcp   # or: wican-ws
  canair config set transport.fallback false            # disable auto-fallback
  canair config set transport.connect_timeout 5.0       # fallback probe timeout (s)
  canair config set transport.fallback_order home,vpn,ap   # comma-separated
  canair config set wican_model pro               # or: classic
  canair config set check_for_updates false

Values are coerced to int/float/bool/null where unambiguous; pass --string to
force a string (e.g. a zero-padded id). Dotted keys create nested mappings.
Comma-separated values are stored as a list for list-valued keys
(transport.fallback_order).

See `canair config example` (config.example.yaml) for every key documented
with inline comments, and {CONFIG_DOCS_URL} for the rendered reference.
"""


def known_key(key: str) -> bool:
    """True if ``key`` is a recognized config key (exact or a known namespace)."""
    if key in KNOWN_KEYS:
        return True
    prefix = key.split(".", 1)[0]
    namespaces = {k.split(".", 1)[0] for k in KNOWN_KEYS if "." in k}
    return prefix in namespaces


def is_device_transport_key(key: str) -> bool:
    """True for a ``devices.<alias>.transport`` key (wildcard middle segment)."""
    parts = key.split(".")
    return len(parts) == 3 and parts[0] == "devices" and parts[2] == "transport"


def enum_values(key: str) -> tuple[str, ...] | None:
    """Return the allowed values for a known enum key, else None."""
    if key == "transport.type" or is_device_transport_key(key):
        return _transport_types()
    if key == "wican_model":
        return _WICAN_MODELS
    if key == "grid_region":
        return _grid_regions()
    if key in SAFETY_GATE_KEYS:
        return _BOOLEANS
    return None


def invalid_value(key: str, value) -> str | None:
    """Return an error message if ``value`` is invalid for a known enum key."""
    if (
        key == "transport.type" or is_device_transport_key(key)
    ) and value not in _transport_types():
        return f"invalid {key} {value!r}; valid: {', '.join(_transport_types())}"
    if key == "wican_model" and str(value).strip().lower() not in _WICAN_MODELS:
        return f"invalid wican_model {value!r}; valid: {', '.join(_WICAN_MODELS)}"
    if key == "grid_region" and str(value).strip().upper() not in _grid_regions():
        return f"invalid grid_region {value!r}; valid: {', '.join(_grid_regions())}"
    if key in SAFETY_GATE_KEYS and not isinstance(value, bool):
        return f"invalid {key} {value!r}; valid: {', '.join(_BOOLEANS)}"
    return None


def missing_key_help() -> None:
    """Print guidance when `config set` is called with no key."""
    messages.error("missing KEY (and VALUE) to set")
    print("\nusage: canair config set KEY VALUE\n", file=sys.stderr)
    print("known keys:", file=sys.stderr)
    print(_keys_block(), file=sys.stderr)
    print("\nexamples:", file=sys.stderr)
    print("  canair config set default_wican home", file=sys.stderr)
    print("  canair config set transport.type slcan-tcp", file=sys.stderr)
    print(
        f"\nSee `canair config example` or {CONFIG_DOCS_URL} for every key.",
        file=sys.stderr,
    )


def missing_value_help(key: str) -> None:
    """Print key-aware guidance when `config set KEY` is missing its value."""
    messages.error(f"missing VALUE for '{key}'")
    print(f"\nusage: canair config set {key} VALUE", file=sys.stderr)

    vals = enum_values(key)
    if vals:
        print(f"\nvalid values: {', '.join(vals)}", file=sys.stderr)
        print(f"\nexample:\n  canair config set {key} {vals[0]}", file=sys.stderr)
        return

    if not known_key(key):
        print(file=sys.stderr)
        messages.note(f"'{key}' is not a recognized config key (see `canair config set --help`)")
    if key.startswith("wican_addresses."):
        print("\nexample:\n  canair config set wican_addresses.home 10.0.2.86", file=sys.stderr)
    else:
        print(f"\nexample:\n  canair config set {key} <value>", file=sys.stderr)
