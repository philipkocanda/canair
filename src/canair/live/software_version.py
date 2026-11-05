"""Reading an ECU's software version — the reading a memory address is valid for.

A dealer reflash moves what an address holds, so a memory map or a memory PID belongs to
one software build. ``canair scan memory`` and ``canair memory dump`` record this
reading with what they read, and a revision's ``software:`` fingerprint is matched
against it (:mod:`canair.variants.fingerprints`), so all three name a build the same way.

The reading is ``RECORD:TEXT`` — the first identity record in :data:`SOFTWARE_RECORDS`
that the ECU answers, then its decoded text (``F195:1.04``). The record is part of the
value, so two ECUs that report different records never look alike.
"""

from __future__ import annotations

from typing import Final

from canair.transport.protocol import Terminal
from canair.uds.echo import request_echo
from canair.uds.protocol import KWP, DiagProtocol

from .identity_decode import decode_identity_payload
from .identity_records import PROTOCOLS

#: The identity records that name a software build, most specific first, per protocol:
#: UDS vehicle-manufacturer then supplier software version, then Hyundai/Kia's F1A0;
#: KWP2000 supplier software version, software number and identifier.
SOFTWARE_RECORDS: Final[dict[str, tuple[str, ...]]] = {
    "uds": ("F189", "F195", "F1A0", "F194"),
    "kwp": ("95", "88", "8D", "8C"),
}


async def read_software_version(
    terminal: Terminal, tx_id: int, protocol: DiagProtocol
) -> str | None:
    """The ECU's software version as ``RECORD:TEXT``, or ``None`` if no record answers.

    Each read is echo-validated, so a late reply to another request is never taken for it.
    """
    proto = KWP if protocol == KWP else "uds"
    wire = PROTOCOLS[proto]
    for record in SOFTWARE_RECORDS[proto]:
        request = f"{wire['prefix']}{record}"
        echo = request_echo(request)
        async with terminal.transaction():
            await terminal.set_header(tx_id)
            resp = await terminal.send_uds(
                request,
                expected_sid=echo[0] if echo else None,
                expected_echo=echo[1] if echo else None,
            )
        if not resp.get("ok"):
            continue
        payload = bytes(resp.get("bytes") or b"")[wire["payload_offset"] :]
        text = decode_identity_payload(payload, "ascii").strip()
        if text and text != "(empty)":
            return f"{record}:{text}"
    return None
