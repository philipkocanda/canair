"""Read the snapshots a UDS ECU stored with the codes it reported: ``19 03``, then ``19 04``.

One ``19 03`` per ECU lists which ``(DTC, record)`` snapshots exist; then each record of a
reported code is read on its own (``19 04 <DTC> <record>``), which lets a one-DID record
describe itself (:mod:`canair.dtc.snapshot`). The decoded records are attached to each code
under ``snapshots``. An ECU that refuses ``19 03`` is not asked again in the run.

Written against the :class:`~canair.transport.protocol.Terminal` surface only, so it runs
on every transport through ``run_session_guarded``. KWP2000 ECUs are skipped: their freeze frames
(``0x12``) are a different service the plan leaves for later.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from canair.uds.protocol import UDS
from canair.uds.response import UdsResponse

from ...dtc.snapshot import SnapshotError, parse_snapshot_ids, parse_snapshot_reply
from ...dtc.snapshot_decode import length_resolver, snapshot_entries
from ...transport.protocol import Terminal

SNAPSHOT_IDS_REQUEST = "1903"
_SID = 0x19
_IDS_SF = 0x03
_RECORD_SF = 0x04
# 59 04 <DTC: 3> <status: 1> <record: 1> …: where a 59 04 reply names its record.
_RECORD_BYTE = 6


@dataclass
class SnapshotRun:
    """What the snapshot step learned across one run: refusals, and whether it was skipped.

    ``refused`` maps an ECU's tx id to why it gave no snapshot list, so a sweep asks each
    ECU once. ``skip`` is polled between requests; when it turns True the remaining
    requests are not sent and ``skipped`` says so.
    """

    skip: Callable[[], bool] = lambda: False
    refused: dict[int, str] = field(default_factory=dict)
    skipped: bool = False
    records_read: int = 0


def ecu_pids(pids_data: Mapping[str, Any] | None, tx_id: int) -> tuple[str, Mapping[str, Any]]:
    """The ECU's name and its ``pids`` index, for lengths and decoding (empty without a profile)."""
    from canair.ecus import ecu_name
    from canair.ecus.pids import build_ecu_index

    name = ecu_name(tx_id)
    if not pids_data:
        return name, {}
    entry = build_ecu_index(dict(pids_data)).get(name.upper()) or {}
    return name, entry.get("pids") or {}


def wants_snapshots(result: Mapping[str, Any]) -> bool:
    """Whether a read result has codes whose snapshots the step can fetch (a UDS ECU)."""
    return result.get("protocol") == UDS and bool(result.get("dtcs"))


async def fetch_snapshots(
    terminal: Terminal,
    tx_id: int,
    result: dict[str, Any],
    pids: Mapping[str, Mapping[str, Any]],
    ecu: str,
    run: SnapshotRun,
    *,
    timeout: float = 3.0,
) -> None:
    """Attach ``snapshots`` to every code in ``result`` that has any, reading them from the ECU.

    ``pids`` is the ECU's ``pids`` index entry (for lengths and decoding) and ``ecu`` its
    name. A refusal of the record list is noted in ``run.refused`` and as
    ``result["snapshot_status"]`` (``{"status": "unsupported", …}``); a code with no stored
    snapshot gets an empty list.
    """
    if not wants_snapshots(result) or tx_id in run.refused or run.skipped:
        return
    if run.skip():
        run.skipped = True
        return

    async with terminal.transaction():
        await terminal.set_header(tx_id)
        listing = await terminal.send_uds(
            SNAPSHOT_IDS_REQUEST,
            timeout=timeout,
            expected_sid=_SID,
            expected_echo=bytes([_IDS_SF]),
        )
    if not listing["ok"]:
        reason = _refusal(listing)
        run.refused[tx_id] = reason
        result["snapshot_status"] = {"status": "unsupported", "detail": reason}
        return
    try:
        stored = parse_snapshot_ids(bytes(listing["bytes"]))
    except SnapshotError as e:
        run.refused[tx_id] = str(e)
        result["snapshot_status"] = {"status": "unreadable", "detail": str(e)}
        return

    length_of = length_resolver(pids, ecu)
    for code in result["dtcs"]:
        raw = bytes.fromhex(code["raw"])
        records = [number for dtc, number in stored if dtc == raw]
        code["snapshots"] = []
        for number in records:
            if run.skip():
                run.skipped = True
                return
            code["snapshots"].append(
                await _read_record(terminal, tx_id, raw, number, length_of, pids, timeout)
            )
            run.records_read += 1


async def _read_record(
    terminal: Terminal,
    tx_id: int,
    dtc: bytes,
    number: int,
    length_of: Callable[[int], int | None],
    pids: Mapping[str, Mapping[str, Any]],
    timeout: float,
) -> dict[str, Any]:
    request = f"1904{dtc.hex().upper()}{number:02X}"
    read_at = datetime.now().strftime("%H:%M:%S.%f")[:-3]
    async with terminal.transaction():
        await terminal.set_header(tx_id)
        reply = await terminal.send_uds(
            request,
            timeout=timeout,
            expected_sid=_SID,
            expected_echo=bytes([_RECORD_SF]) + dtc,
        )
    if not reply["ok"]:
        return {"record": number, "error": _refusal(reply)}
    data = bytes(reply["bytes"])
    stamp = {"request": request, "time": read_at}
    # The echo check covers the DTC only; a reply for another record would decode, log and
    # save as this one, so it is reported without its bytes.
    if len(data) > _RECORD_BYTE and data[_RECORD_BYTE] != number:
        got = data[_RECORD_BYTE]
        return {"record": number, "error": f"reply is for record {got:02X}, not {number:02X}"}
    try:
        parsed = parse_snapshot_reply(data, length_of, single_record=True)
    except SnapshotError as e:
        return {"record": number, "hex": data.hex().upper(), "error": str(e), **stamp}
    if not parsed.records:
        return {"record": number, "hex": data.hex().upper(), "dids": [], **stamp}
    return {**snapshot_entries(parsed.records[0], data.hex(), pids), **stamp}


def _refusal(response: UdsResponse) -> str:
    nrc = response.get("nrc")
    if nrc is not None:
        desc = response.get("nrc_desc") or ""
        return f"NRC 0x{nrc:02X}" + (f" ({desc})" if desc else "")
    return str(response.get("error") or "no response")
