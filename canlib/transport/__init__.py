"""Alternate CAN transports for canair (raw-CAN backends).

The default live path is the raw SLCAN-over-TCP backend
(:class:`canlib.transport.slcan_tcp.SlcanTcpBus`, transport ``slcan-tcp``),
which drives the bus with client-side ISO-TP. The ELM327 WebSocket terminal
(:mod:`canlib.terminal`, transport ``wican-ws``) is the alternative where the
dongle does ISO-TP. Transports are described once in
:data:`canlib.transport.config.TRANSPORTS`; register a new spec there to add
another backend.
"""

from .channel import Channel, WebSocketChannel
from .config import (
    DEFAULT_TRANSPORT,
    TRANSPORTS,
    VALID_TRANSPORTS,
    TransportConfig,
    TransportError,
    TransportSpec,
    resolve_transport,
    resolve_transport_candidates,
)
from .elm327_terminal import Elm327Terminal
from .errors import (
    connect_error_detail,
    describe_transport_error,
    is_transport_error,
    transport_error_types,
)
from .fallback import select_reachable_transport, wait_for_reachable
from .protocol import Terminal
from .raw_terminal import RawTerminal
from .slcan_tcp import SlcanTcpBus, format_slcan_frame, parse_slcan_frame
from .uds_raw import RawUdsClient, response_id

__all__ = [
    "DEFAULT_TRANSPORT",
    "TRANSPORTS",
    "VALID_TRANSPORTS",
    "Channel",
    "Elm327Terminal",
    "RawTerminal",
    "RawUdsClient",
    "SlcanTcpBus",
    "Terminal",
    "TransportConfig",
    "TransportError",
    "TransportSpec",
    "WebSocketChannel",
    "connect_error_detail",
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
