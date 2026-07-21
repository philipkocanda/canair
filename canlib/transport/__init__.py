"""Alternate CAN transports for canair (raw-CAN backends).

The default live path is the ELM327 WebSocket terminal (:mod:`canlib.terminal`).
This package holds raw-CAN backends built on python-can — currently SLCAN over
TCP (:class:`canlib.transport.slcan_tcp.SlcanTcpBus`).
"""

from .slcan_tcp import SlcanTcpBus, format_slcan_frame, parse_slcan_frame
from .uds_raw import RawUdsClient, response_id

__all__ = [
    "RawUdsClient",
    "SlcanTcpBus",
    "format_slcan_frame",
    "parse_slcan_frame",
    "response_id",
]
