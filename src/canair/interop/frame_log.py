"""Extract UDS data-read replies from a recorded CAN frame log into capture records.

A frame log taken from a diagnostic bus while *some other tool* polled the car (a
Bluetooth dongle running an app's custom PIDs, a workshop tester) holds exactly the
exchanges ``canair read --save`` would have recorded, minus the recorder. This turns
them back into captures: :func:`~canair.uds.isotp_passive.reassemble` rebuilds the
ISO-TP messages, then each ECU's positive reply to a data read becomes one record.

What counts as a reply, and why only that:

- ``62 DID DID …`` (ReadDataByIdentifier) is filed as ``22DIDDID`` and ``61 LID …``
  (KWP2000 readDataByLocalIdentifier) as ``21LID``. A positive reply names what it
  answers, so no request has to be paired for it to be filed correctly. Pairing on
  the request instead mis-files replies whenever the tool pipelines requests or a
  broadcast scan is interleaved.
- The one thing a reply does *not* name is a **batched** request (``22 D1 D2 …``): its
  reply starts with ``D1`` and carries ``D2`` at a data-dependent offset, so filing it
  under ``D1`` would be a misfiled capture. Only the requests can tell, so every
  data-read request to an ECU is kept until it is answered (or could no longer be: see
  :data:`_REPLY_WINDOW_S`), and a reply starting with the first DID of a batch still
  outstanding is skipped, not guessed, however many requests were sent after it. A
  reply that splits into several DIDs whose lengths earlier replies showed is skipped
  too, which catches a batch whose request frame is missing from the log.
- A negative reply (``7F sid nrc``) does not repeat the identifier either, so it is
  filed only on request, and only against an unambiguous single-identifier request: the
  oldest outstanding request of that service, since an ECU answers its requests in turn.
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
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import TYPE_CHECKING

from canair.captures.types import CaptureRecord
from canair.ecus import EcuRegistryEntry, ecu_protocol, rx_addr_str, rx_for_tx
from canair.uds.isotp_passive import ReassemblyStats, reassemble
from canair.uds.multi_did import split_chain
from canair.uds.nrc import is_response_pending, nrc_description
from canair.uds.services import (
    KWP_READ_DATA_BY_LOCAL_IDENTIFIER,
    NEGATIVE_RESPONSE_SID,
    READ_DATA_BY_IDENTIFIER,
    positive_response_sid,
)

if TYPE_CHECKING:
    import can

# Replies from an id outside the registry are only worth mentioning when they look like
# the answer to a data read, otherwise every broadcast frame would be "unregistered".
_DATA_READ_REPLY_SIDS = frozenset(
    {
        positive_response_sid(READ_DATA_BY_IDENTIFIER),
        positive_response_sid(KWP_READ_DATA_BY_LOCAL_IDENTIFIER),
        NEGATIVE_RESPONSE_SID,
    }
)


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


# How long a request can still be answered: P2*server_max, the longest an ECU may take
# after a responsePending (ISO 14229-2). A request older than that is never answered,
# so a batch the ECU ignored stops making later single reads of its first DID look
# batched.
_REPLY_WINDOW_S = 5.0


@dataclass
class _Outstanding:
    """One ECU's data-read requests not yet answered, oldest first."""

    requests: list[tuple[float, _Request]] = field(default_factory=list)
    lengths: dict[str, int] = field(default_factory=dict)  # DID4 -> data bytes, from replies

    def expire(self, now: float) -> None:
        self.requests = [(t, r) for t, r in self.requests if now - t <= _REPLY_WINDOW_S]

    def take(self, match: Callable[[_Request], bool]) -> _Request | None:
        """Remove and return the oldest outstanding request ``match`` accepts."""
        for i, (_t, request) in enumerate(self.requests):
            if match(request):
                del self.requests[i]
                return request
        return None

    def refresh(self, service: int, now: float) -> None:
        """responsePending: the oldest request of ``service`` gets a new reply window."""
        for i, (_t, request) in enumerate(self.requests):
            if request.service == service:
                self.requests[i] = (now, request)
                return


def extract_replies(
    frames: Iterable[can.Message],
    ecus: Mapping[int, EcuRegistryEntry],
    *,
    keep_negative: bool = False,
) -> Extraction:
    """Find every data-read reply in ``frames`` from an ECU in ``ecus`` (keyed by TX id)."""
    result = Extraction()
    rx_to_tx = {rx_for_tx(tx, ecus): tx for tx in ecus}
    outstanding: dict[tuple[int | str | None, int], _Outstanding] = {}

    # Losses are tallied on the reply ids only: a request lost on its way to the ECU
    # is not a capture that went missing.
    for msg in reassemble(frames, result.reassembly, tracked=set(rx_to_tx)):
        payload = msg.payload
        if msg.arbitration_id in ecus:
            pending = outstanding.setdefault((msg.channel, msg.arbitration_id), _Outstanding())
            pending.expire(msg.timestamp)
            request = _parse_request(payload)
            if request is not None:
                pending.requests.append((msg.timestamp, request))
            continue

        tx = rx_to_tx.get(msg.arbitration_id)
        if tx is None:
            if payload[0] in _DATA_READ_REPLY_SIDS:
                result.unregistered[msg.arbitration_id] += 1
            continue

        pending = outstanding.setdefault((msg.channel, tx), _Outstanding())
        pending.expire(msg.timestamp)
        sid = payload[0]
        notes: str | None = None
        if sid == positive_response_sid(READ_DATA_BY_IDENTIFIER) and len(payload) >= 3:
            echoed = payload[1:3]
            batch = pending.take(lambda r, e=echoed: _is_batch_for(r, e))
            if batch is not None or split_chain(payload, pending.lengths.get) is not None:
                result.batched_skipped += 1
                continue
            pending.take(
                lambda r, e=echoed: r.service == READ_DATA_BY_IDENTIFIER and r.first_id == e
            )
            pending.lengths[echoed.hex().upper()] = len(payload) - 3
            pid = f"22{echoed.hex().upper()}"
        elif sid == positive_response_sid(KWP_READ_DATA_BY_LOCAL_IDENTIFIER) and len(payload) >= 2:
            echoed = payload[1:2]
            pending.take(
                lambda r, e=echoed: (
                    r.service == KWP_READ_DATA_BY_LOCAL_IDENTIFIER and r.first_id == e
                )
            )
            pid = f"21{echoed.hex().upper()}"
        elif (
            sid == NEGATIVE_RESPONSE_SID
            and len(payload) == 3
            and payload[1] in (KWP_READ_DATA_BY_LOCAL_IDENTIFIER, READ_DATA_BY_IDENTIFIER)
        ):
            service = payload[1]
            if is_response_pending(payload, service):
                pending.refresh(service, msg.timestamp)
                continue
            request = pending.take(lambda r, s=service: r.service == s)
            if not (keep_negative and request is not None and request.count == 1):
                result.negatives_skipped += 1
                continue
            pid = f"{request.service:02X}{request.first_id.hex().upper()}"
            protocol = ecu_protocol(tx, ecus)
            notes = f"NRC 0x{payload[2]:02X} {nrc_description(payload[2], protocol)}"
        else:
            result.other_services[sid] += 1
            continue
        result.replies.append(
            ExtractedReply(msg.timestamp, rx_addr_str(tx, ecus), pid, payload.hex().upper(), notes)
        )
    return result


def _parse_request(payload: bytes) -> _Request | None:
    """The identifier(s) of a data-read request, or None for any other request."""
    if payload[0] == READ_DATA_BY_IDENTIFIER and len(payload) >= 3 and len(payload) % 2 == 1:
        return _Request(READ_DATA_BY_IDENTIFIER, payload[1:3], (len(payload) - 1) // 2)
    if payload[0] == KWP_READ_DATA_BY_LOCAL_IDENTIFIER and len(payload) == 2:
        return _Request(KWP_READ_DATA_BY_LOCAL_IDENTIFIER, payload[1:2], 1)
    return None


def _is_batch_for(request: _Request, echoed_id: bytes) -> bool:
    """Is ``request`` a batched service-22 read whose reply starts ``62 echoed_id``?"""
    return (
        request.service == READ_DATA_BY_IDENTIFIER
        and request.count > 1
        and request.first_id == echoed_id
    )


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
