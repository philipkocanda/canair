"""Explicit, config-driven CAN transport selection.

canair talks to the CAN bus through one of several *transports*, chosen
explicitly (never auto-detected or auto-switched). Each transport is described
once in the :data:`TRANSPORTS` registry below, so adding another (e.g. a native
``socketcan`` backend) is a matter of registering a new :class:`TransportSpec` —
selection, validation, and ``canair status`` all read from the registry rather
than hard-coding transport names.

Selection precedence (highest first): CLI flag (``--transport``/``--wican``) >
the ``transport:`` block in the user config > :data:`DEFAULT_TRANSPORT`.
``slcan-tcp`` is the canonical default: it works on both the WiCAN Pro and the
classic WiCAN (any TCP-SLCAN gateway) and drives the bus with client-side
ISO-TP, so every command supports it. Port and bitrate have no dedicated CLI
flags — they come from the config ``transport:`` block, falling back to the
device's live config where relevant.
"""

from __future__ import annotations

from dataclasses import dataclass


class TransportError(ValueError):
    """Raised when the transport configuration is invalid."""


@dataclass(frozen=True)
class TransportSpec:
    """Static description of a transport type (drives selection + status).

    Register one per transport in :data:`TRANSPORTS`. ``raw`` marks a raw-CAN
    backend (python-can bus + client-side ISO-TP, as opposed to an ELM327-style
    terminal where the dongle does ISO-TP). ``summary`` is the human one-liner
    shown by ``canair status`` — describe the *mechanism* and any transport-
    specific capabilities, not the command list (nearly every command runs over
    every transport via a common terminal interface).
    """

    type: str
    raw: bool
    summary: str


# Registry of known transports. Add a new entry here to teach canair a new
# backend; everything else (validation, defaulting, status display) follows.
TRANSPORTS: dict[str, TransportSpec] = {
    "slcan-tcp": TransportSpec(
        type="slcan-tcp",
        raw=True,
        summary=(
            "raw SLCAN over TCP; canair runs ISO-TP/UDS client-side "
            "(pipelined) — all diagnostic commands + passive sniff"
        ),
    ),
    "wican-ws": TransportSpec(
        type="wican-ws",
        raw=False,
        summary=(
            "ELM327 terminal over WebSocket; the dongle runs ISO-TP — "
            "all diagnostic commands (no passive sniff)"
        ),
    ),
}

VALID_TRANSPORTS = tuple(TRANSPORTS)

# Canonical default when nothing is configured (see module docstring).
DEFAULT_TRANSPORT = "slcan-tcp"


@dataclass(frozen=True)
class TransportConfig:
    """A resolved transport selection."""

    type: str
    host: str | None = None
    port: int | None = None
    bitrate: int | None = None

    @property
    def spec(self) -> TransportSpec | None:
        """The registered :class:`TransportSpec`, or None for an unknown type."""
        return TRANSPORTS.get(self.type)

    @property
    def is_raw(self) -> bool:
        """True for raw-CAN transports (python-can bus + client-side ISO-TP)."""
        spec = self.spec
        # Unknown types default to raw (the ELM terminal is the sole exception).
        return spec.raw if spec is not None else self.type != "wican-ws"

    @property
    def is_elm(self) -> bool:
        """True for the ELM327-terminal transport (WiCAN Pro WebSocket)."""
        return not self.is_raw

    @property
    def summary(self) -> str | None:
        """Human one-liner describing the transport (from the registry)."""
        spec = self.spec
        return spec.summary if spec is not None else None

    @property
    def is_wican_http(self) -> bool:
        """True when the device is a WiCAN reachable over its HTTP config API.

        Both current transports point at a WiCAN (ws terminal or its SLCAN
        socket), so its ``/load_config`` / ``/check_status`` endpoints are
        queryable. (A future non-WiCAN transport, e.g. socketcan, would not be.)
        """
        return self.host is not None

    def describe(self) -> str:
        loc = self.host or "?"
        if self.port:
            loc = f"{loc}:{self.port}"
        return f"{self.type} ({loc})"

    def resolve_device_defaults(self, profile_bitrate: int | None = None) -> tuple[int, int]:
        """Effective ``(port, bitrate)`` for a raw-CAN connection.

        Bitrate precedence (highest first): the explicit config ``transport:``
        value, then the active profile's ``can_bitrate`` (``profile_bitrate``,
        the vehicle's own bus speed), then the device's live config (queried
        **only** when it's a WiCAN reachable over HTTP — :attr:`is_wican_http`),
        then the conventional SLCAN default of 500 kbit/s. Port follows the same
        config → device → default (3333) chain. Keeping the vehicle bus speed in
        the profile means switching profiles switches bitrate, without editing
        the global config block. A non-WiCAN SLCAN gateway (e.g. socketcan/other)
        has no HTTP endpoint, so it skips the probe entirely.
        """
        port, bitrate = self.port, self.bitrate
        if bitrate is None:
            bitrate = profile_bitrate
        if (port is None or bitrate is None) and self.is_wican_http and self.host is not None:
            cfg = self._wican_device_config()
            if port is None:
                try:
                    port = int(cfg.get("port", 3333) or 3333)
                except (TypeError, ValueError):
                    port = 3333
            if bitrate is None:
                bitrate = _parse_datarate(cfg.get("can_datarate"))
        return port or 3333, bitrate or 500000

    def _wican_device_config(self) -> dict:
        """Best-effort read of a WiCAN's live ``/load_config`` (empty on failure)."""
        import sys

        from ..wican_api import resolve_wican_url
        from ..wican_mode import load_config

        assert self.host is not None  # only called when is_wican_http (host set)
        try:
            return load_config(resolve_wican_url(self.host)) or {}
        except Exception as e:  # best-effort — fall back to conventional defaults
            print(f"  (could not read device config for defaults: {e})", file=sys.stderr)
            return {}


def _parse_datarate(value) -> int | None:
    """Parse a WiCAN ``can_datarate`` like '500K' / '1M' / '250000' to an int bitrate."""
    if value is None:
        return None
    s = str(value).strip().upper().replace("BIT", "").rstrip("/S")
    try:
        if s.endswith("M"):
            return int(float(s[:-1]) * 1_000_000)
        if s.endswith("K"):
            return int(float(s[:-1]) * 1_000)
        return int(s)
    except ValueError:
        return None


def _resolve_host(name: str | None) -> str | None:
    """Map a ``--wican`` alias to its IP, or pass an IP/host through."""
    if not name:
        return None
    return _wican_addresses().get(name, name)


def _wican_addresses():  # small indirection so tests can monkeypatch cheaply
    from ..constants import WICAN_ADDRESSES

    return WICAN_ADDRESSES


def _wican_devices():  # small indirection so tests can monkeypatch cheaply
    from ..config import wican_devices

    return wican_devices()


def _fallback_settings():  # small indirection so tests can monkeypatch cheaply
    from ..config import fallback_settings

    return fallback_settings()


def _first(*vals):
    """First value that is not None (used for CLI > device > block precedence)."""
    for v in vals:
        if v is not None:
            return v
    return None


def _int(v):
    return int(v) if v is not None else None


def _check_type(ttype: str) -> None:
    """Validate a transport type name, raising :class:`TransportError` if unknown."""
    if ttype not in VALID_TRANSPORTS:
        raise TransportError(f"Unknown transport '{ttype}'. Valid: {', '.join(VALID_TRANSPORTS)}.")


def _wican_ws_pro_error() -> TransportError:
    return TransportError(
        "The 'wican-ws' transport (ELM327 WebSocket terminal) is a WiCAN "
        "Pro-only feature; your config sets wican_model: classic. Use the "
        "default 'slcan-tcp' transport instead (works on the classic WiCAN). "
        "If this device is actually a Pro, run: canair config set wican_model pro"
    )


def _build_candidate(args, device, block) -> TransportConfig:
    """Resolve one device into a :class:`TransportConfig`.

    Per-field precedence (highest first): the CLI flag, then the device's own
    ``transport``/``port``/``bitrate``, then the global ``transport:`` block,
    then the default. Raises :class:`TransportError` for an unknown type.
    """

    def arg(name):
        return getattr(args, name, None) if args is not None else None

    ttype = _first(arg("transport"), device.transport, block.get("type"), DEFAULT_TRANSPORT)
    _check_type(ttype)
    port = _int(_first(arg("port"), device.port, block.get("port")))
    bitrate = _int(_first(arg("bitrate"), device.bitrate, block.get("bitrate")))
    return TransportConfig(type=ttype, host=device.host, port=port, bitrate=bitrate)


def resolve_transport_candidates(args=None) -> list[TransportConfig]:
    """Resolve the ordered list of transports to try (primary first).

    ``candidates[0]`` is the explicitly selected device (``--wican`` > config
    ``transport.host`` > ``default_wican``), with per-device transport applied.
    When auto-fallback is enabled (config ``transport.fallback``, default true,
    unless ``--no-fallback``), the remaining configured devices follow — ordered
    by ``transport.fallback_order`` when set, else definition order. An explicit
    ``--wican X`` always stays first; the ``fallback_order`` only sequences the
    rest.
    """
    from ..config import DeviceEntry, load_config

    def arg(name):
        return getattr(args, name, None) if args is not None else None

    raw_block = load_config().get("transport")
    block = raw_block if isinstance(raw_block, dict) else {}
    devices, default_alias = _wican_devices()

    # Determine the primary device (candidate 0).
    explicit = arg("wican")
    if explicit:
        primary_key: str | None = explicit
        primary_dev = devices.get(explicit) or DeviceEntry(host=_resolve_host(explicit) or explicit)
    elif block.get("host"):
        primary_key = None
        primary_dev = DeviceEntry(host=str(block["host"]))
    else:
        primary_key = default_alias
        primary_dev = devices.get(default_alias) or DeviceEntry(
            host=_resolve_host(default_alias) or default_alias
        )

    primary = _build_candidate(args, primary_dev, block)
    if primary.type == "wican-ws" and not _is_pro():
        raise _wican_ws_pro_error()
    candidates = [primary]

    enabled, _timeout, order = _fallback_settings()
    if not enabled or arg("no_fallback"):
        return candidates

    ordered = order or [default_alias, *[a for a in devices if a != default_alias]]
    seen_hosts = {primary.host}
    for alias in ordered:
        if alias == primary_key:
            continue
        dev = devices.get(alias)
        if dev is None:
            continue
        try:
            cand = _build_candidate(args, dev, block)
        except TransportError:
            continue
        # Skip a wican-ws candidate we can't actually use, and any duplicate host.
        if cand.type == "wican-ws" and not _is_pro():
            continue
        if cand.host in seen_hosts:
            continue
        seen_hosts.add(cand.host)
        candidates.append(cand)
    return candidates


def _is_pro() -> bool:
    from ..config import is_wican_pro

    return is_wican_pro()


def resolve_transport(args=None) -> TransportConfig:
    """Resolve the active (primary) transport from CLI args + user config.

    ``args`` is an argparse Namespace that may expose ``transport`` and ``wican``
    (both optional). Port and bitrate are taken from the selected device or the
    config ``transport:`` block (no dedicated CLI flags). Raises
    :class:`TransportError` for an unknown transport type. Equivalent to the
    first entry of :func:`resolve_transport_candidates`.
    """
    return resolve_transport_candidates(args)[0]
