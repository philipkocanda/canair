"""Explicit, config-driven CAN transport selection.

canair talks to the CAN bus through one of a few *transports*, chosen
explicitly (never auto-detected or auto-switched):

* ``wican-ws``  — WiCAN Pro WebSocket ELM327 terminal (device does ISO-TP).
* ``slcan-tcp`` — SLCAN over TCP (classic WiCAN / any TCP-SLCAN gateway);
                  diagnostics use client-side ISO-TP via
                  :class:`canlib.transport.uds_raw.RawUdsClient`.

Selection precedence (highest first): CLI flag (``--transport``/``--wican``/
``--port``/``--bitrate``) > the ``transport:`` block in the user config >
the legacy ``wican_addresses``/``default_wican`` fallback (→ ``wican-ws``).
"""

from __future__ import annotations

from dataclasses import dataclass

VALID_TRANSPORTS = ("wican-ws", "slcan-tcp")


class TransportError(ValueError):
    """Raised when the transport configuration is invalid."""


@dataclass(frozen=True)
class TransportConfig:
    """A resolved transport selection."""

    type: str
    host: str | None = None
    port: int | None = None
    bitrate: int | None = None

    @property
    def is_raw(self) -> bool:
        """True for raw-CAN transports (python-can bus + client-side ISO-TP)."""
        return self.type != "wican-ws"

    @property
    def is_elm(self) -> bool:
        """True for the ELM327-terminal transport (WiCAN Pro WebSocket)."""
        return self.type == "wican-ws"

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


def _resolve_host(name: str | None) -> str | None:
    """Map a ``--wican`` alias to its IP, or pass an IP/host through."""
    if not name:
        return None
    return _wican_addresses().get(name, name)


def _wican_addresses():  # small indirection so tests can monkeypatch cheaply
    from ..constants import WICAN_ADDRESSES

    return WICAN_ADDRESSES


def resolve_transport(args=None) -> TransportConfig:
    """Resolve the active transport from CLI args + user config.

    ``args`` is an argparse Namespace that may expose ``transport``, ``wican``,
    ``port``, ``bitrate`` (all optional). Raises :class:`TransportError` for an
    unknown transport type.
    """
    from ..config import load_config

    def arg(name):
        return getattr(args, name, None) if args is not None else None

    raw_block = load_config().get("transport")
    block = raw_block if isinstance(raw_block, dict) else {}

    ttype = arg("transport") or block.get("type") or "wican-ws"
    if ttype not in VALID_TRANSPORTS:
        raise TransportError(
            f"Unknown transport '{ttype}'. Valid: {', '.join(VALID_TRANSPORTS)}."
        )

    # Host: explicit --wican > config transport.host > default_wican alias.
    if arg("wican"):
        host = _resolve_host(arg("wican"))
    elif block.get("host"):
        host = str(block["host"])
    else:
        from ..constants import DEFAULT_WICAN

        host = _resolve_host(DEFAULT_WICAN)

    def _int(v):
        return int(v) if v is not None else None

    port = _int(arg("port")) or _int(block.get("port"))
    bitrate = _int(arg("bitrate")) or _int(block.get("bitrate"))

    return TransportConfig(type=ttype, host=host, port=port, bitrate=bitrate)
