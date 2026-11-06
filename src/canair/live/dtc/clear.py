"""``canair dtc ECU --clear``: erase an ECU's fault memory (``0x14``) and log what it held."""

from __future__ import annotations

import json

from canair.profile.model import Profile
from canair.render import ansi, messages
from canair.uds.nrc import (
    KWP_NRC_SUB_FUNCTION_NOT_SUPPORTED_OR_INVALID_FORMAT,
    NRC_REQUEST_OUT_OF_RANGE,
)
from canair.uds.protocol import KWP
from canair.uds.requests import clear_dtc_request
from canair.uds.response import UdsResponse
from canair.uds.services import CLEAR_DIAGNOSTIC_INFORMATION
from canair.uds.subfunctions import DtcGroup

from ...ecus.names import ecu_display
from ...transport.protocol import Terminal
from ..hold import held_session
from .advice import clear_nrc_hints, no_response_hints
from .codes import resolve_protocol
from .reading import _read_one
from .scan_log import _log_manual_clear, logged_snapshots
from .snapshots import SnapshotRun, ecu_pids, fetch_snapshots, wants_snapshots

# A KWP2000 ECU that rejects the ISO 14230-3 "all groups" (FF00) this way may take SSF
# 14230-3's FFFF instead; both mean every DTC, so retrying the other one is safe.
_KWP_GROUP_REJECTED = frozenset(
    {NRC_REQUEST_OUT_OF_RANGE, KWP_NRC_SUB_FUNCTION_NOT_SUPPORTED_OR_INVALID_FORMAT}
)


async def mode_dtc_clear(
    terminal: Terminal,
    tx_id: int,
    group: int | None = None,
    protocol: str = "auto",
    session: bool = False,
    wake: bool = False,
    as_json: bool = False,
    verbose: bool = False,
    log: bool = False,
    label: str | None = None,
    snapshots: bool = True,
    pids_data: dict | None = None,
    *,
    profile: Profile,
) -> bool:
    """Clear stored DTCs via ClearDiagnosticInformation (0x14); True when the ECU did.

    ``group`` None clears every DTC: ``FFFFFF`` on UDS, ``FF00`` on KWP2000, then ``FFFF``
    if the ECU rejects that. An explicit group is sent as given. Mutates ECU fault memory;
    confirmation is the caller's job.

    When ``log`` is set, the current codes are read first (so the log records what was
    cleared) and the clear is written to the DTC log, so a later scan reports these codes
    as wiped by the clear rather than as self-cleared. An ECU that does not answer that
    read is not sent the clear. With ``snapshots`` too, the snapshots a UDS ECU stored with
    those codes are read and logged with the clear, since clearing erases them and nothing
    else would keep them.
    """
    proto = resolve_protocol(protocol, tx_id, profile=profile)
    kwp = proto == KWP
    ecu = ecu_display(tx_id, profile=profile)
    await terminal.set_header(tx_id)

    async with held_session(terminal, tx_id, profile=profile, enabled=session, wake=wake):
        pre_codes = None
        pre_snapshots: dict = {}
        if log:
            pre = await _read_one(terminal, tx_id, 0xFF, protocol, verbose, profile=profile)
            if "error" in pre:
                _report_failure(
                    {"ecu": ecu, "protocol": proto, "cleared": False, "error": pre["error"]},
                    f"{ecu} did not answer the DTC read before the clear ({pre['error']}); "
                    "nothing was cleared",
                    no_response_hints(tx_id, pre, profile=profile, pids_data=pids_data, wake=wake),
                    as_json,
                )
                return False
            if "dtcs" in pre:
                pre_codes = [d["dtc"] for d in pre["dtcs"]]
            if snapshots and wants_snapshots(pre):
                ecu_name, pids = ecu_pids(pids_data, tx_id, profile=profile)
                await fetch_snapshots(
                    terminal, tx_id, pre, pids, ecu_name, SnapshotRun(), profile=profile
                )
                pre_snapshots = logged_snapshots(pre["dtcs"])
            await terminal.set_header(tx_id)

        cmd = clear_dtc_request(group, kwp=kwp)
        if not as_json:
            print(f"\n  DTC clear: {ecu}")
            print(f"  Command: {cmd} (ClearDiagnosticInformation, {proto})\n")
        response = await _send_clear(terminal, cmd)
        if kwp and group is None and response.get("nrc") in _KWP_GROUP_REJECTED:
            cmd = clear_dtc_request(DtcGroup.KWP_ALL_DTCS_SSF, kwp=True)
            if not as_json:
                messages.note(
                    f"the ECU rejected group FF00 (ISO 14230-3); retrying with {cmd} (SSF 14230-3)"
                )
            response = await _send_clear(terminal, cmd)

        base = {
            "ecu": ecu,
            "protocol": proto,
            "command": cmd,
            "group": f"0x{cmd[2:]}",  # the group as sent, after the SID
        }
        if response["ok"]:
            if log:
                _log_manual_clear(
                    tx_id, proto, base["group"], pre_codes, label, pre_snapshots, profile=profile
                )
            _report_cleared(base, pre_codes, pre_snapshots, log, as_json)
            return True
        if response.get("nrc") is not None:
            nrc = response["nrc"]
            _report_failure(
                {
                    **base,
                    "cleared": False,
                    "nrc": f"0x{nrc:02X}",
                    "nrc_desc": response["nrc_desc"],
                },
                f"{ecu} refused the clear: NRC 0x{nrc:02X} ({response['nrc_desc']})",
                clear_nrc_hints(nrc, tx_id, profile=profile, pids_data=pids_data, session=session),
                as_json,
            )
            return False
        error = response.get("error", "unknown")
        _report_failure(
            {**base, "cleared": False, "error": error},
            f"{ecu} did not answer the clear ({error}); it may or may not have cleared",
            no_response_hints(tx_id, response, profile=profile, pids_data=pids_data, wake=wake),
            as_json,
        )
        return False


async def _send_clear(terminal: Terminal, cmd: str) -> UdsResponse:
    return await terminal.send_uds(cmd, timeout=5.0, expected_sid=CLEAR_DIAGNOSTIC_INFORMATION)


def _report_cleared(
    base: dict, pre_codes: list[str] | None, pre_snapshots: dict, log: bool, as_json: bool
) -> None:
    if as_json:
        cleared = {**base, "cleared": True, "cleared_codes": pre_codes}
        if pre_snapshots:
            cleared["erased_snapshots"] = pre_snapshots
        print(json.dumps(cleared))
        return
    print(f"  {ansi.GREEN}✓ DTCs cleared{ansi.RESET} (group {base['group']}).")
    if pre_codes:
        print(f"    {len(pre_codes)} code(s) cleared: {', '.join(pre_codes)}")
    elif pre_codes == []:
        print("    (no codes were present)")
    if pre_snapshots:
        n = sum(len(records) for records in pre_snapshots.values())
        print(f"    {n} snapshot record(s) read before clearing, kept in the log")
    if log:
        print(f"  {ansi.DIM}Logged clear to the DTC log{ansi.RESET}")
    print()


def _report_failure(document: dict, error: str, hints: list[str], as_json: bool) -> None:
    """The JSON document on stdout under ``--json``; the error and its hints on stderr."""
    if as_json:
        print(json.dumps(document))
    messages.error(error)
    for hint in hints:
        messages.hint(hint)
