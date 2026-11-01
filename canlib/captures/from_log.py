"""Extract UDS data-read replies from a recorded CAN frame log into capture records.

A frame log taken from a diagnostic bus while *some other tool* polled the car (a
Bluetooth dongle running an app's custom PIDs, a workshop tester) holds exactly the
exchanges ``canair read --save`` would have recorded, minus the recorder. This turns
them back into captures: :func:`~canlib.uds.isotp_passive.reassemble` rebuilds the
ISO-TP messages, then each ECU's positive reply to a data read becomes one record.

What counts as a reply, and why only that:

- ``62 DID DID …`` (ReadDataByIdentifier) is filed as ``22DIDDID`` and ``61 LID …``
  (KWP2000 readDataByLocalIdentifier) as ``21LID``. A positive reply names what it
  answers, so no request has to be paired for it to be filed correctly. Pairing on
  the request instead mis-files replies whenever the tool pipelines requests or a
  broadcast scan is interleaved.
- The one thing a reply does *not* name is a **batched** request (``22 D1 D2 …``): its
  reply starts with ``D1`` and carries ``D2`` at a data-dependent offset, so filing it
  under ``D1`` would be a misfiled capture. The tester's last request to that ECU is
  the only way to tell, so it is tracked and such replies are skipped, not guessed.
- A negative reply (``7F sid nrc``) does not repeat the identifier either, so it is
  filed only on request, and only against an unambiguous single-identifier request.
  ``responsePending`` (``0x78``) is never a result, always followed by one.
- OBD-II mode 01 replies (``41 PID …``) are left out, and reported with the other
  services. A multi-PID request (``01 0C 0D``) is answered by one reply interleaving
  each PID with its data, so only the request can say a reply holds more than one PID.
  A scan tool sends mode 01 on the functional id, which names no ECU the per-ECU
  request tracking above could pair with it.

Only ECUs in the profile's registry are extracted, because the registry is what turns
a CAN id into a response address and a name; replies from other ids are counted so the
caller can say which to register.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import TYPE_CHECKING

from canlib.captures.types import CaptureRecord
from canlib.ecus import EcuRegistryEntry, rx_addr_str, rx_for_tx
from canlib.uds.isotp_passive import ReassemblyStats, reassemble
from canlib.uds.parse import nrc_description

if TYPE_CHECKING:
    import can

_READ_BY_LOCAL_ID = 0x21
_READ_BY_ID = 0x22
_NEGATIVE = 0x7F
_RESPONSE_PENDING = 0x78
# Replies from an id outside the registry are only worth mentioning when they look like
# the answer to a data read, otherwise every broadcast frame would be "unregistered".
_DATA_READ_REPLY_SIDS = frozenset({_READ_BY_ID + 0x40, _READ_BY_LOCAL_ID + 0x40, _NEGATIVE})


@dataclass(frozen=True)
class ExtractedReply:
    """One extracted reply: a capture record minus its wall-clock time."""

    timestamp: float  # the log's own clock, seconds
    rx: str
    pid: str
    payload: str
    notes: str | None = None


@dataclass
class Extraction:
    """The replies found in a log, and an account of everything that was left out."""

    replies: list[ExtractedReply] = field(default_factory=list)
    reassembly: ReassemblyStats = field(default_factory=ReassemblyStats)
    negatives_skipped: int = 0
    batched_skipped: int = 0
    other_services: Counter[int] = field(default_factory=Counter)  # reply SID -> count
    unregistered: Counter[int] = field(default_factory=Counter)  # CAN id -> count

    @property
    def lost(self) -> int:
        """Reply messages known to be missing frames (see :class:`ReassemblyStats`)."""
        return self.reassembly.lost


@dataclass(frozen=True)
class _Request:
    service: int
    first_id: bytes
    count: int


def extract_replies(
    frames: Iterable[can.Message],
    ecus: Mapping[int, EcuRegistryEntry],
    *,
    keep_negative: bool = False,
) -> Extraction:
    """Find every data-read reply in ``frames`` from an ECU in ``ecus`` (keyed by TX id)."""
    result = Extraction()
    rx_to_tx = {rx_for_tx(tx, ecus): tx for tx in ecus}
    last_request: dict[tuple[int | str | None, int], _Request | None] = {}

    tracked = set(ecus) | set(rx_to_tx)
    for msg in reassemble(frames, result.reassembly, tracked=tracked):
        payload = msg.payload
        if msg.arbitration_id in ecus:
            last_request[(msg.channel, msg.arbitration_id)] = _parse_request(payload)
            continue

        tx = rx_to_tx.get(msg.arbitration_id)
        if tx is None:
            if payload[0] in _DATA_READ_REPLY_SIDS:
                result.unregistered[msg.arbitration_id] += 1
            continue

        request = last_request.get((msg.channel, tx))
        sid = payload[0]
        notes: str | None = None
        if sid == _READ_BY_ID + 0x40 and len(payload) >= 3:
            if _is_batched(request, _READ_BY_ID, payload[1:3]):
                result.batched_skipped += 1
                continue
            pid = f"22{payload[1:3].hex().upper()}"
        elif sid == _READ_BY_LOCAL_ID + 0x40 and len(payload) >= 2:
            pid = f"21{payload[1:2].hex().upper()}"
        elif sid == _NEGATIVE and len(payload) == 3 and payload[1] in (0x21, 0x22):
            if payload[2] == _RESPONSE_PENDING:
                continue
            if not (keep_negative and request and _answers_single_read(request, payload[1])):
                result.negatives_skipped += 1
                continue
            pid = f"{request.service:02X}{request.first_id.hex().upper()}"
            notes = f"NRC 0x{payload[2]:02X} {nrc_description(payload[2])}"
        else:
            result.other_services[sid] += 1
            continue
        result.replies.append(
            ExtractedReply(msg.timestamp, rx_addr_str(tx, ecus), pid, payload.hex().upper(), notes)
        )
    return result


def _parse_request(payload: bytes) -> _Request | None:
    """The identifier(s) of a data-read request, or None for any other request."""
    if payload[0] == _READ_BY_ID and len(payload) >= 3 and len(payload) % 2 == 1:
        return _Request(_READ_BY_ID, payload[1:3], (len(payload) - 1) // 2)
    if payload[0] == _READ_BY_LOCAL_ID and len(payload) == 2:
        return _Request(_READ_BY_LOCAL_ID, payload[1:2], 1)
    return None


def _is_batched(request: _Request | None, service: int, echoed_id: bytes) -> bool:
    return (
        request is not None
        and request.service == service
        and request.count > 1
        and request.first_id == echoed_id
    )


def _answers_single_read(request: _Request, service: int) -> bool:
    """True when ``request`` is a one-identifier read of ``service``, so an NRC is unambiguous."""
    return request.service == service and request.count == 1


def stamp(replies: Iterable[ExtractedReply], start: datetime) -> list[CaptureRecord]:
    """Capture records with ``time`` set, ``start`` being the first reply's wall-clock time.

    Raises ``ValueError`` if the replies run past midnight: a capture's ``time`` has no
    date of its own, so the day the session is filed under would be wrong for the rest.
    """
    records: list[CaptureRecord] = []
    first: float | None = None
    for reply in replies:
        if first is None:
            first = reply.timestamp
        when = start + timedelta(seconds=reply.timestamp - first)
        if when.date() != start.date():
            raise ValueError(
                f"the log runs past midnight (reply at +{reply.timestamp - first:.0f}s from a "
                f"start of {start:%H:%M:%S}); pick an earlier --time or narrow the log with "
                "--from-ts/--until-ts"
            )
        record: CaptureRecord = {
            "rx": reply.rx,
            "pid": reply.pid,
            "payload": reply.payload,
            "time": when.strftime("%H:%M:%S.") + f"{when.microsecond // 1000:03d}",
        }
        if reply.notes:
            record["notes"] = reply.notes
        records.append(record)
    return records
