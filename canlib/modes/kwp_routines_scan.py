"""KWP2000 RoutineControl discovery scanner (RequestRoutineResults, 0x33).

KWP2000 (ISO 14230-3) splits RoutineControl across three services:

    0x31 StartRoutineByLocalIdentifier          -- ACTUATES (DO NOT scan!)
    0x32 StopRoutineByLocalIdentifier
    0x33 RequestRoutineResultsByLocalIdentifier  -- read-only, SAFE

This is critically different from UDS, where ``0x31`` is RoutineControl and
sub-function ``0x03`` (requestRoutineResults) is the safe read. On a KWP2000 ECU
``31 03 …`` parses as *StartRoutine LID 0x03* — it can drive hardware. So for
KWP2000 ECUs (BMS, VCU, MCU, LDC, AAF) we probe **0x33** instead, which only asks
"what was the result of routine X?" and never starts anything.

    request:  33 {LID}          (8-bit routine local identifier)
    positive: 73 {LID} {status/result …}

The generic scan loop lives in :mod:`canlib.modes.discovery_scan`; this module
supplies the 0x33-specific probe, classification and writeback.
"""

from __future__ import annotations

import sys
from typing import NamedTuple

from ..terminal import WiCANTerminal
from .discovery_scan import DiscoveryProbe, mode_discovery_scan

# NRCs that still indicate "routine local identifier exists"
NRC_EXISTS_HINTS = {
    0x22,  # conditionsNotCorrect — present, preconditions unmet
    0x24,  # requestSequenceError — present, never started (no results yet)
    0x33,  # securityAccessDenied — present, locked
    0x72,  # generalProgrammingFailure — present, error state
    0x78,  # requestCorrectlyReceivedResponsePending
}
NRC_ABSENT = 0x31  # requestOutOfRange — routine LID not present
NRC_SERVICE_ABSENT = 0x11  # serviceNotSupported — ECU doesn't do 0x33 → abort
NRC_WRONG_SESSION = 0x7F


class KwpRoutineHit(NamedTuple):
    """A single positive KWP2000 routine-results probe result.

    ``rid`` names the 8-bit routine local identifier (kept as ``rid`` so the
    shared ``routines:`` writeback and hit helpers work unchanged).
    """

    rid: int
    session: str  # "default" or "extended"
    response_hex: str  # empty if NRC-based hit
    nrc: int | None  # None for positive response
    nrc_desc: str | None


async def probe_kwp_routine_results(terminal: WiCANTerminal, lid: int) -> dict:
    """Send ``33 {LID}`` (RequestRoutineResultsByLocalIdentifier) — read-only.

    Validates the ``0x73`` response SID and guards against the stale-frame -1
    shift by rejecting a LID echo mismatch.
    """
    if not 0 <= lid <= 0xFF:
        raise ValueError(f"KWP2000 routine local identifier must be a single byte, got 0x{lid:X}")
    req = f"33{lid:02X}"
    resp = await terminal.send_uds(req, timeout=2.0, expected_sid=0x33)
    if resp.get("ok"):
        b = resp.get("bytes") or b""
        if len(b) >= 2 and b[1] != lid:
            return {
                "raw": resp.get("raw", ""),
                "ok": False,
                "error": f"LID echo mismatch: got 0x{b[1]:02X} != requested 0x{lid:02X}",
            }
    return resp


def classify(response: dict) -> tuple[str, int | None]:
    """Classify a probe response.

    "positive" | "exists" | "absent" | "service-absent" | "wrong-session" | "error".
    """
    if response.get("ok"):
        return "positive", None
    nrc = response.get("nrc")
    if nrc is None:
        return "error", None
    if nrc == NRC_WRONG_SESSION:
        return "wrong-session", nrc
    if nrc == NRC_SERVICE_ABSENT:
        return "service-absent", nrc
    if nrc == NRC_ABSENT:
        return "absent", nrc
    if nrc in NRC_EXISTS_HINTS:
        return "exists", nrc
    # Unknown NRC — treat as a hit so we don't miss anything
    return "exists", nrc


def _make_hit(lid, session, response_hex, nrc, nrc_desc) -> KwpRoutineHit:
    return KwpRoutineHit(lid, session, response_hex, nrc, nrc_desc)


def _write_hit(ecu_name: str, hit: KwpRoutineHit) -> None:
    from ..pids_edit import append_routines_block

    try:
        # KWP routine local identifiers are a single byte → 2-hex-digit keys.
        append_routines_block(ecu_name, [hit], key_width=2)
    except Exception as exc:
        print(f"  [{ecu_name}] ERROR writing YAML: {exc}", file=sys.stderr)


KWP_ROUTINES_PROBE = DiscoveryProbe(
    name="KWP2000 RoutineResults (0x33)",
    scan_type="routines-kwp",
    id_label="LID",
    id_width=1,
    service=0x33,
    probe=lambda terminal, lid: probe_kwp_routine_results(terminal, lid),
    classify=classify,
    make_hit=_make_hit,
    request_display=lambda lid: f"33 {lid:02X}",
    write_hit=_write_hit,
)


async def mode_kwp_routines_scan(
    terminal: WiCANTerminal,
    pids_data: dict,
    ecus: list[str],
    lid_range: tuple[int, int] | None = None,
    throttle_ms: int = 150,
    verbose: bool = False,
    write_yaml: bool = True,
    session: bool = False,
    wake: bool = False,
    session_mode: str = "03",
) -> dict[str, list[KwpRoutineHit]]:
    """Scan KWP2000 routine local identifiers via the safe 0x33 results read.

    Thin wrapper over :func:`discovery_scan.mode_discovery_scan`. Default LID range
    is the full single-byte space ``00–FF``. Never starts a routine (0x31).
    """
    return await mode_discovery_scan(
        terminal,
        KWP_ROUTINES_PROBE,
        pids_data,
        ecus=ecus,
        id_range=lid_range,
        default_range=(0x00, 0xFF),
        throttle_ms=throttle_ms,
        verbose=verbose,
        write_yaml=write_yaml,
        session=session,
        wake=wake,
        session_mode=session_mode,
    )
