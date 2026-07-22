"""RoutineControl (UDS 0x31) discovery scanner.

Probes a range of Routine IDs on body ECUs using sub-function 0x03
(requestRoutineResults), which is side-effect-free: it asks "what was the
outcome of routine X the last time it ran?" and never starts anything.

Request byte order is IMPORTANT:
    31 {SF} {RID_HI} {RID_LO}    -- SF=03 for requestRoutineResults

(NOT ``31 {RID} {SF}`` — that's how the generic 0x22 / 0x2F scanner lays
things out.)

Response interpretation:
    positive (71 ...)            -- routine ID exists, result returned
    NRC 0x31 (requestOutOfRange) -- routine ID does not exist on this ECU
    NRC 0x24 (requestSequenceError) / 0x22 (conditionsNotCorrect)
                                 -- routine exists but has never been run
                                    or conditions are wrong — still a hit
    NRC 0x33 (securityAccessDenied) -- routine exists but locked behind 0x27
    NRC 0x7F (serviceNotSupportedInActiveSession) -- try extended session
    NRC 0x11 (serviceNotSupported)  -- ECU doesn't implement 0x31 at all

Safety:
    * Sub-function 0x03 only. NEVER 0x01 startRoutine on a live car.
    * Defaults target IGPM/BCM/HVAC. SKM is explicitly excluded.
    * RID range defaults to F000-F0FF (256 IDs per ECU).

The generic scan loop lives in :mod:`canlib.modes.discovery_scan`; this module
supplies the 0x31-specific probe, classification and writeback.
"""

from __future__ import annotations

import sys
from typing import NamedTuple

from ..terminal import WiCANTerminal
from .discovery_scan import DiscoveryProbe, mode_discovery_scan

# Routine sub-functions
SF_START = 0x01  # startRoutine — DO NOT USE
SF_STOP = 0x02  # stopRoutine
SF_RESULTS = 0x03  # requestRoutineResults — safe

# NRCs that still indicate "routine exists"
NRC_EXISTS_HINTS = {
    0x22,  # conditionsNotCorrect — routine present, preconditions unmet
    0x24,  # requestSequenceError — present, never started
    0x33,  # securityAccessDenied — present, locked
    0x72,  # generalProgrammingFailure — present, error state
    0x78,  # requestCorrectlyReceivedResponsePending — present
}

# NRCs that indicate "absent" (do not record as a hit)
NRC_ABSENT = {
    0x11,  # serviceNotSupported
    0x12,  # subFunctionNotSupported
    0x31,  # requestOutOfRange
}

# NRC that suggests retrying in extended session
NRC_WRONG_SESSION = 0x7F


class RoutineHit(NamedTuple):
    """A single positive probe result."""

    rid: int
    session: str  # "default" or "extended"
    response_hex: str  # empty if NRC-based hit
    nrc: int | None  # None for positive response
    nrc_desc: str | None


async def probe_routine(
    terminal: WiCANTerminal,
    rid: int,
    sub_function: int = SF_RESULTS,
) -> dict:
    """Send ``31 {SF} {RID_HI} {RID_LO}`` and return the parsed response."""
    req = f"31{sub_function:02X}{rid:04X}"
    return await terminal.send_uds(req, timeout=2.0)


def classify(response: dict) -> tuple[str, int | None]:
    """Classify a probe response.

    Returns (category, nrc) where category is one of:
        "positive"      — ECU answered 71 ...
        "exists"        — NRC indicates the RID is present (22/24/33/72/78)
        "absent"        — NRC indicates the RID is not there (11/12/31)
        "wrong-session" — NRC 7F, retry in extended
        "error"         — transport/timeout/other
    """
    if response.get("ok"):
        return "positive", None
    nrc = response.get("nrc")
    if nrc is None:
        return "error", None
    if nrc == NRC_WRONG_SESSION:
        return "wrong-session", nrc
    if nrc in NRC_ABSENT:
        return "absent", nrc
    if nrc in NRC_EXISTS_HINTS:
        return "exists", nrc
    # Unknown NRC — treat as a hit so we don't miss anything
    return "exists", nrc


def _make_hit(rid, session, response_hex, nrc, nrc_desc) -> RoutineHit:
    return RoutineHit(rid, session, response_hex, nrc, nrc_desc)


def _write_hit(ecu_name: str, hit: RoutineHit) -> None:
    from ..pids_edit import append_routines_block

    try:
        append_routines_block(ecu_name, [hit])
    except Exception as exc:
        print(f"  [{ecu_name}] ERROR writing YAML: {exc}", file=sys.stderr)


ROUTINES_PROBE = DiscoveryProbe(
    name="RoutineControl (0x31)",
    scan_type="routines",
    id_label="RID",
    id_width=2,
    service=0x31,
    probe=probe_routine,
    classify=classify,
    make_hit=_make_hit,
    request_display=lambda rid: f"31 03 {rid >> 8:02X} {rid & 0xFF:02X}",
    write_hit=_write_hit,
)


async def mode_routines_scan(
    terminal: WiCANTerminal,
    pids_data: dict,
    ecus: list[str],
    rid_range: tuple[int, int] = (0xF000, 0xF0FF),
    throttle_ms: int = 150,
    verbose: bool = False,
    write_yaml: bool = True,
) -> dict[str, list[RoutineHit]]:
    """Scan RoutineControl IDs on one or more ECUs (safe SF 0x03).

    Thin wrapper over :func:`discovery_scan.mode_discovery_scan` with the 0x31
    probe. Hits are written incrementally to each ``pids/<ecu>.yaml`` under a
    ``routines:`` section so they survive a mid-scan disconnect.
    """
    return await mode_discovery_scan(
        terminal,
        ROUTINES_PROBE,
        pids_data,
        ecus=ecus,
        id_range=rid_range,
        default_range=rid_range,
        throttle_ms=throttle_ms,
        verbose=verbose,
        write_yaml=write_yaml,
    )
