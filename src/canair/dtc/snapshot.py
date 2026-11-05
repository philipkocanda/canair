"""DTC snapshot replies (UDS ``19 03`` / ``19 04``): parse them into records of DIDs.

I/O-free and profile-free, like :mod:`canair.analysis.counters`: bytes in, records out.
Decoding a DID's data against the profile is :mod:`canair.dtc.snapshot_decode`.

A ``59 04`` reply (reportDTCSnapshotRecordByDTCNumber, ISO 14229-1) is::

    59 04 <DTC: 3> <status: 1> { <record: 1> <count: 1> { <DID: 2> <data: n> }×count }*

The data length ``n`` of each DID is not in the reply: the client has to know it. When the
request asked for one record (``19 04 <DTC> <record>``) the last DID of that record takes
whatever remains, which makes the common one-DID record self-describing; every other DID's
length comes from the ``length_of(did)`` callback :func:`parse_snapshot_reply` is given. A DID whose
length cannot be resolved is never guessed: parsing stops there and the rest of the reply
is kept, raw, on the record (:attr:`SnapshotRecord.undecoded`).

A ``59 03`` reply (reportDTCSnapshotIdentification) lists which records exist::

    59 03 { <DTC: 3> <record: 1> }*
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from canair.uds.services import READ_DTC_INFORMATION, positive_response_sid
from canair.uds.subfunctions import DtcReportType

READ_DID_SERVICE = "22"
_POSITIVE = positive_response_sid(READ_DTC_INFORMATION)
# ISO 14229-1: record number FF in a request means "every record"; it never names one.
ALL_RECORDS = 0xFF


class SnapshotError(ValueError):
    """A reply that is not a well-formed ``59 03``/``59 04`` (wrong header, truncated)."""


def did_of_read(pid: str) -> int | None:
    """The DID a service-22 PID key reads (``"224710"`` → ``0x4710``), else None."""
    pid = str(pid).strip().upper()
    if len(pid) != 6 or not pid.startswith(READ_DID_SERVICE):
        return None
    try:
        return int(pid[2:], 16)
    except ValueError:
        return None


def read_pid(did: int) -> str:
    """The service-22 PID key that reads ``did`` (``0x4710`` → ``"224710"``)."""
    return f"{READ_DID_SERVICE}{did:04X}"


@dataclass(frozen=True)
class SnapshotDid:
    """One DID and its data, as stored in a snapshot record."""

    did: int
    data: bytes


@dataclass(frozen=True)
class SnapshotRecord:
    """One snapshot record: its number and the DIDs it holds.

    ``undecoded`` is the rest of the reply from the first DID whose length could not be
    resolved, and ``unresolved_did`` names that DID; both are empty when the record
    parsed completely.
    """

    number: int
    dids: tuple[SnapshotDid, ...]
    undecoded: bytes = b""
    unresolved_did: int | None = None

    @property
    def complete(self) -> bool:
        return self.unresolved_did is None


@dataclass(frozen=True)
class SnapshotReply:
    """A parsed ``59 04`` reply: the DTC it is for, its status, and its records."""

    dtc: bytes
    status: int
    records: tuple[SnapshotRecord, ...]


def parse_snapshot_ids(data: bytes) -> list[tuple[bytes, int]]:
    """``(DTC, record number)`` pairs from a ``59 03`` reply, in reply order."""
    _check_header(data, DtcReportType.REPORT_DTC_SNAPSHOT_IDENTIFICATION)
    body = data[2:]
    if len(body) % 4:
        raise SnapshotError(
            f"59 03 reply has {len(body)} bytes after its header, not a multiple of 4 "
            "(DTC + record number)"
        )
    return [(bytes(body[i : i + 3]), body[i + 3]) for i in range(0, len(body), 4)]


def parse_snapshot_reply(
    data: bytes,
    length_of: Callable[[int], int | None],
    *,
    single_record: bool = False,
) -> SnapshotReply:
    """Parse a ``59 04`` reply into its records.

    ``length_of(did)`` returns a DID's data length, or None when it is not known.
    ``single_record`` says the request named one record, so the last DID of the (only)
    record takes the remaining bytes without asking ``length_of``. A reply with no records (the DTC has no
    snapshot stored) parses to an empty ``records``.
    """
    _check_header(data, DtcReportType.REPORT_DTC_SNAPSHOT_RECORD_BY_DTC_NUMBER)
    if len(data) < 6:
        raise SnapshotError(f"59 04 reply is {len(data)} bytes, shorter than its 6-byte header")
    dtc, status = bytes(data[2:5]), data[5]
    records: list[SnapshotRecord] = []
    i = 6
    while i < len(data):
        if len(data) - i < 2:
            raise SnapshotError(f"59 04 reply ends inside a record header at byte {i}")
        number, count = data[i], data[i + 1]
        i += 2
        dids: list[SnapshotDid] = []
        for k in range(count):
            if len(data) - i < 2:
                raise SnapshotError(f"record {number:02X} ends inside DID {k + 1} of {count}")
            did = (data[i] << 8) | data[i + 1]
            i += 2
            # The last DID of the only record of a one-record reply owns what remains: the
            # reply itself says so, so no declared length is needed (or consulted).
            if single_record and not records and k == count - 1:
                n: int | None = len(data) - i
            else:
                n = length_of(did)
            if n is None:
                records.append(
                    SnapshotRecord(number, tuple(dids), bytes(data[i - 2 :]), unresolved_did=did)
                )
                return SnapshotReply(dtc, status, tuple(records))
            if n > len(data) - i:
                raise SnapshotError(
                    f"record {number:02X} DID {did:04X} needs {n} bytes, {len(data) - i} remain"
                )
            dids.append(SnapshotDid(did, bytes(data[i : i + n])))
            i += n
        records.append(SnapshotRecord(number, tuple(dids)))
    return SnapshotReply(dtc, status, tuple(records))


def did_replies(reply_hex: str) -> list[tuple[str, str]]:
    """``(read PID, live-equivalent reply)`` for a one-DID record of a one-record ``59 04``.

    ``59 04 … 4710 <data>`` gives ``("224710", "624710<data>")``: the reply a live read of
    the DID would have given, which is how its signals decode a snapshot. Only a record
    holding a single DID describes itself; a record with several DIDs needs every DID's
    length but the last, which this profile-free helper does not know, so it gives nothing
    (as does a malformed reply).
    """
    try:
        parsed = parse_snapshot_reply(
            bytes.fromhex(reply_hex), lambda _did: None, single_record=True
        )
    except (SnapshotError, ValueError):
        return []
    return [
        (read_pid(d.did), f"62{d.did:04X}{d.data.hex().upper()}")
        for record in parsed.records
        for d in record.dids
    ]


def _check_header(data: bytes, sub_function: int) -> None:
    if len(data) < 2 or data[0] != _POSITIVE or data[1] != sub_function:
        got = bytes(data[:2]).hex(" ").upper() or "nothing"
        raise SnapshotError(f"expected a 59 {sub_function:02X} reply, got {got}")
