"""Alternate CAN transports for canair (raw-CAN backends).

The default live path is the ELM327 WebSocket terminal (:mod:`canlib.terminal`).
This package holds raw-CAN backends built on python-can — currently SLCAN over
TCP (:class:`canlib.transport.slcan_tcp.SlcanTcpBus`).
"""

from .slcan_tcp import SlcanTcpBus, format_slcan_frame, parse_slcan_frame

__all__ = ["SlcanTcpBus", "format_slcan_frame", "parse_slcan_frame"]
