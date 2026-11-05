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

The generic scan loop lives in :mod:`canair.live.discovery_scan`; this module
supplies the 0x33-specific probe, classification and writeback.
"""

from __future__ import annotations

import sys
from typing import NamedTuple

from canair.live.discovery_scan import DiscoveryProbe, mode_discovery_scan
from canair.safety.quirks import NISSAN_C0_SESSION, has_quirk
from canair.transport.protocol import Terminal
from canair.uds.nrc import (
    KWP_NRC_CONDITIONS_NOT_CORRECT_OR_REQUEST_SEQUENCE_ERROR,
    KWP_NRC_TRANSFER_ABORTED,
    NOT_IN_ACTIVE_SESSION_NRCS,
    NRC_REQUEST_OUT_OF_RANGE,
    NRC_RESPONSE_PENDING,
    NRC_SECURITY_ACCESS_DENIED,
    NRC_SERVICE_NOT_SUPPORTED,
)
from canair.uds.response import UdsResponse
from canair.uds.services import KWP_REQUEST_ROUTINE_RESULTS_BY_LOCAL_IDENTIFIER

# NRCs that still indicate "routine local identifier exists". Any NRC not named in classify()
# counts as a hit too, so 0x24 (requestSequenceError on UDS, reserved by SSF 14230-3) still is.
NRC_EXISTS_HINTS = {
    KWP_NRC_CONDITIONS_NOT_CORRECT_OR_REQUEST_SEQUENCE_ERROR,  # present, preconditions unmet
    NRC_SECURITY_ACCESS_DENIED,  # present, locked
    KWP_NRC_TRANSFER_ABORTED,  # present, error state
    NRC_RESPONSE_PENDING,
}


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


async def probe_kwp_routine_results(terminal: Terminal, lid: int) -> UdsResponse:
    """Send ``33 {LID}`` (RequestRoutineResultsByLocalIdentifier) — read-only.

    Validates the ``0x73`` response SID and guards against the stale-frame -1
    shift by rejecting a LID echo mismatch.
    """
    if not 0 <= lid <= 0xFF:
        raise ValueError(f"KWP2000 routine local identifier must be a single byte, got 0x{lid:X}")
    req = f"33{lid:02X}"
    resp = await terminal.send_uds(
        req, timeout=2.0, expected_sid=KWP_REQUEST_ROUTINE_RESULTS_BY_LOCAL_IDENTIFIER
    )
    if resp.get("ok"):
        b = resp.get("bytes")
        if b and len(b) >= 2 and b[1] != lid:
            return {
                "raw": resp.get("raw", ""),
                "ok": False,
                "error": f"LID echo mismatch: got 0x{b[1]:02X} != requested 0x{lid:02X}",
            }
    return resp


def classify(response: UdsResponse) -> tuple[str, int | None]:
    """Classify a probe response.

    "positive" | "exists" | "absent" | "service-absent" | "wrong-session" | "error".
    """
    if response.get("ok"):
        return "positive", None
    nrc = response.get("nrc")
    if nrc is None:
        return "error", None
    # KWP2000 says "not in this session" with 0x80; the UDS 0x7F is kept for ECUs that mix
    # the two. Read as "exists", 0x80 would record every LID as a hit.
    if nrc in NOT_IN_ACTIVE_SESSION_NRCS:
        return "wrong-session", nrc
    if nrc == NRC_SERVICE_NOT_SUPPORTED:  # the ECU has no 0x33: abort
        return "service-absent", nrc
    if nrc == NRC_REQUEST_OUT_OF_RANGE:  # the routine LID is not there
        return "absent", nrc
    if nrc in NRC_EXISTS_HINTS:
        return "exists", nrc
    # Unknown NRC — treat as a hit so we don't miss anything
    return "exists", nrc


def _make_hit(lid, session, response_hex, nrc, nrc_desc) -> KwpRoutineHit:
    return KwpRoutineHit(lid, session, response_hex, nrc, nrc_desc)


def _write_hit(ecu_name: str, hit: KwpRoutineHit) -> None:
    from canair.yaml_edit.pids import append_routines_block

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
    service=KWP_REQUEST_ROUTINE_RESULTS_BY_LOCAL_IDENTIFIER,
    probe=lambda terminal, lid: probe_kwp_routine_results(terminal, lid),
    classify=classify,
    make_hit=_make_hit,
    request_display=lambda lid: f"33 {lid:02X}",
    write_hit=_write_hit,
)


def needs_confirmation(pids_data: dict) -> bool:
    """True when this profile's 0x33 sweep is not known to be side-effect-free.

    Nissan reshapes KWP2000 services (its 0x30 layout makes the ISO "release"
    probe an actuation), and nothing documents what its 0x33 does, so the sweep
    is confirm-first there like any other operation that may change ECU state.
    """
    return has_quirk(pids_data, NISSAN_C0_SESSION)


UNPROVEN_WARNING = (
    "this profile's KWP2000 ECUs use Nissan's own service layouts, and what service "
    "0x33 does there is undocumented — this 256-LID sweep is not proven to be "
    "side-effect-free."
)


async def mode_kwp_routines_scan(
    terminal: Terminal,
    pids_data: dict,
    ecus: list[str],
    lid_range: tuple[int, int] | None = None,
    throttle_ms: int = 150,
    verbose: bool = False,
    write_yaml: bool = True,
    session: bool = False,
    wake: bool = False,
    session_mode: str = "03",
    confirmed: bool = False,
) -> dict[str, list[KwpRoutineHit]]:
    """Scan KWP2000 routine local identifiers via the safe 0x33 results read.

    Thin wrapper over :func:`discovery_scan.mode_discovery_scan`. Default LID range
    is the full single-byte space ``00–FF``. Never starts a routine (0x31). Where
    :func:`needs_confirmation` holds, nothing is sent unless ``confirmed`` (the
    caller's prompt or ``--yes``).
    """
    if needs_confirmation(pids_data) and not confirmed:
        print(
            f"  Refusing the KWP2000 routine scan: {UNPROVEN_WARNING} Re-run with --yes "
            "to accept that.",
            file=sys.stderr,
        )
        return {}
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
