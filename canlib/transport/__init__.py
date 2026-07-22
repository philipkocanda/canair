"""Alternate CAN transports for canair (raw-CAN backends).

The default live path is the raw SLCAN-over-TCP backend
(:class:`canlib.transport.slcan_tcp.SlcanTcpBus`, transport ``slcan-tcp``),
which drives the bus with client-side ISO-TP. The ELM327 WebSocket terminal
(:mod:`canlib.terminal`, transport ``wican-ws``) is the alternative where the
dongle does ISO-TP. Transports are described once in
:data:`canlib.transport.config.TRANSPORTS`; register a new spec there to add
another backend.
"""

from .config import (
    DEFAULT_TRANSPORT,
    TRANSPORTS,
    VALID_TRANSPORTS,
    TransportConfig,
    TransportError,
    TransportSpec,
    resolve_transport,
)
from .raw_terminal import RawTerminal
from .slcan_tcp import SlcanTcpBus, format_slcan_frame, parse_slcan_frame
from .uds_raw import RawUdsClient, response_id

__all__ = [
    "DEFAULT_TRANSPORT",
    "TRANSPORTS",
    "VALID_TRANSPORTS",
    "RawTerminal",
    "RawUdsClient",
    "SlcanTcpBus",
    "TransportConfig",
    "TransportError",
    "TransportSpec",
    "format_slcan_frame",
    "parse_slcan_frame",
    "resolve_transport",
    "response_id",
]
