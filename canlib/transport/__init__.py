"""Alternate CAN transports for canair (raw-CAN backends).

The default live path is the raw SLCAN-over-TCP backend
(:class:`canlib.transport.raw.slcan_tcp.SlcanTcpBus`, transport ``slcan-tcp``),
which drives the bus with client-side ISO-TP. The ELM327 WebSocket terminal
(:mod:`canlib.transport.elm327.wican`, transport ``wican-ws``) is the alternative where the
dongle does ISO-TP. Transports are described once in
:data:`canlib.transport.config.TRANSPORTS`; register a new spec there to add
another backend.

Belongs: moving bytes to and from the car, ISO-TP framing, the ELM327 and raw SLCAN engines, the
per-exchange policies (keepalive, link latency, wake, timeouts, frame counts), the transport
choice and fallback, the session manager and the per-ECU response log.
Not here: what a live operation does with a terminal (``modes/``), what may be sent
(``safety/``) and the adapter's HTTP API (``device/``).
Imports: ``safety``, ``uds``, ``ecus``, ``device``, ``config`` and ``core``. Operations see only
the ``Terminal`` protocol, never a concrete terminal.
"""

from canlib.transport.elm327.channel import Channel, TcpChannel, WebSocketChannel
from canlib.transport.elm327.tcp import Elm327TcpTerminal
from canlib.transport.elm327.terminal import Elm327Terminal
from canlib.transport.raw.slcan_tcp import SlcanTcpBus, format_slcan_frame, parse_slcan_frame
from canlib.transport.raw.terminal import RawTerminal
from canlib.transport.raw.uds_client import RawUdsClient, response_id

from .config import (
    DEFAULT_ELM327_TCP_PORT,
    DEFAULT_TRANSPORT,
    TRANSPORTS,
    VALID_TRANSPORTS,
    ConnectionOptions,
    TransportConfig,
    TransportError,
    TransportOverrides,
    TransportSpec,
    TransportType,
    resolve_transport,
    resolve_transport_candidates,
)
from .errors import describe_transport_error, is_transport_error, transport_error_types
from .fallback import select_reachable_transport, wait_for_reachable
from .protocol import Terminal

__all__ = [
    "DEFAULT_ELM327_TCP_PORT",
    "DEFAULT_TRANSPORT",
    "TRANSPORTS",
    "VALID_TRANSPORTS",
    "Channel",
    "ConnectionOptions",
    "Elm327TcpTerminal",
    "Elm327Terminal",
    "RawTerminal",
    "RawUdsClient",
    "SlcanTcpBus",
    "TcpChannel",
    "Terminal",
    "TransportConfig",
    "TransportError",
    "TransportOverrides",
    "TransportSpec",
    "TransportType",
    "WebSocketChannel",
    "describe_transport_error",
    "format_slcan_frame",
    "is_transport_error",
    "parse_slcan_frame",
    "resolve_transport",
    "resolve_transport_candidates",
    "response_id",
    "select_reachable_transport",
    "transport_error_types",
    "wait_for_reachable",
]
