"""Alternate CAN transports for canair (raw-CAN backends).

The default live path is the ELM327 WebSocket terminal (:mod:`canlib.terminal`).
This package holds raw-CAN backends built on python-can — currently SLCAN over
TCP (:class:`canlib.transport.slcan_tcp.SlcanTcpBus`).
"""

from .config import VALID_TRANSPORTS, TransportConfig, TransportError, resolve_transport
from .raw_terminal import RawTerminal
from .slcan_tcp import SlcanTcpBus, format_slcan_frame, parse_slcan_frame
from .uds_raw import RawUdsClient, response_id

__all__ = [
    "VALID_TRANSPORTS",
    "RawTerminal",
    "RawUdsClient",
    "SlcanTcpBus",
    "TransportConfig",
    "TransportError",
    "format_slcan_frame",
    "parse_slcan_frame",
    "resolve_transport",
    "response_id",
]
