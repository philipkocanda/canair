"""The WiCAN device registry in the config, cross-device fallback and the WiCAN model."""

from __future__ import annotations

import math
from dataclasses import dataclass

from canair.config.file import load_config

# Fallback WiCAN address when nothing is configured (WiCAN AP mode).
_DEFAULT_ADDRESSES = {"ap": "192.168.80.1"}


_DEFAULT_WICAN_KEY = "ap"


# Auto-fallback defaults (the `transport:` block, keys fallback / connect_timeout
# / fallback_order). Fallback is on by default.
_DEFAULT_FALLBACK = True


# Per-device liveness probe (seconds). This is a compromise: it has to be long
# enough that a *reachable* device is never mistaken for a dead one, and short
# enough that a genuinely dead one is skipped quickly, because fallback pays it
# once per candidate. 2s was too tight for a cellular link, where the radio's
# idle-to-connected transition can take a second or two before the SYN even
# leaves — canair would declare the device unreachable while it was fine.
_DEFAULT_CONNECT_TIMEOUT = 5.0


# WiCAN hardware model. AutoPID vehicle profiles and the wican-ws ELM327
# terminal are Pro-only; the classic (non-Pro) WiCAN only supports raw SLCAN.
WICAN_MODELS = ("pro", "classic")


_DEFAULT_WICAN_MODEL = "pro"


def wican_settings() -> tuple[dict[str, str], str]:
    """Return (addresses, default_alias) from config or built-in fallbacks.

    Back-compat shim over :func:`wican_devices`: flattens the device map to
    ``{alias: host}`` for callers that only need the host per alias.
    """
    devices, default = wican_devices()
    return {alias: dev.host for alias, dev in devices.items()}, default


def wican_addresses() -> dict[str, str]:
    """``{alias: host}`` for every configured device.

    Prefer this over the removed ``constants.WICAN_ADDRESSES`` module constant:
    that was resolved through a module-level ``__getattr__`` (PEP 562), which
    returned an *untyped* value at every import site and — because a plain
    ``from … import`` triggers it — read the user's config file at **import**
    time. That fired during pytest collection, before any fixture could isolate
    it. A function makes both the type and the lazy resolution explicit.
    """
    return wican_settings()[0]


def default_wican() -> str:
    """The configured ``default_wican`` alias (see :func:`wican_addresses`)."""
    return wican_settings()[1]


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
