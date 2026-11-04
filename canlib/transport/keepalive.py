"""Which ECUs a TesterPresent keepalive is wasted on.

An ECU that answers ``3E 00`` with NRC 0x11 (serviceNotSupported) has no TesterPresent in any
session, so a keepalive to it only costs bus time (the Ioniq's BMS). Every sender of a
keepalive stops asking such an ECU after its first refusal, through the one test here. NRC
0x7F, 0x80 and 0x22 do not count: those say "not in this session" or "not now", which a
session can change.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from canlib.uds.nrc import NRC_SERVICE_NOT_SUPPORTED
from canlib.uds.services import NEGATIVE_RESPONSE_SID

from ..log import log_event

TESTER_PRESENT_SID = 0x3E


def unsupported(resp: Mapping[str, Any]) -> bool:
    """Is this parsed reply to a ``3E 00`` the ECU saying it has no TesterPresent?"""
    return resp.get("nrc") == NRC_SERVICE_NOT_SUPPORTED


def unsupported_bytes(reply: bytes | None) -> bool:
    """The same test on a raw reply payload (``7F 3E 11``)."""
    return (
        reply is not None
        and len(reply) >= 3
        and reply[0] == NEGATIVE_RESPONSE_SID
        and reply[1] == TESTER_PRESENT_SID
        and reply[2] == NRC_SERVICE_NOT_SUPPORTED
    )


def report_unsupported(tx_id: int, *, transport: str | None = None) -> None:
    """Say once, in the diagnostics log, that ``tx_id`` gets no more keepalives."""
    log_event(
        "config",
        f"0x{tx_id:03X} answered TesterPresent with NRC 0x11: no more keepalives to it",
        level=20,  # logging.INFO
        transport=transport,
        ecu=f"0x{tx_id:03X}",
    )
