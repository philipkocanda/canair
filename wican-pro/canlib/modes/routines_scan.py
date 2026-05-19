"""RoutineControl (UDS 0x31) discovery scanner.

Probes a range of Routine IDs on body ECUs using sub-function 0x03
(requestRoutineResults), which is side-effect-free: it asks "what was the
outcome of routine X the last time it ran?" and never starts anything.

Request byte order is IMPORTANT:
    31 {SF} {RID_HI} {RID_LO}    -- SF=03 for requestRoutineResults

(NOT ``31 {RID} {SF}`` — that's how the generic 0x22 / 0x2F scanner lays
things out.)

Response interpretation:
    positive (7F 31 ...)         -- routine ID exists, result returned
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
"""

from __future__ import annotations

import asyncio
import sys
from typing import Callable, NamedTuple

from ..scan_state import ScanStateWriter
from ..terminal import WiCANTerminal

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


async def scan_ecu(
    terminal: WiCANTerminal,
    ecu_name: str,
    tx_id: int,
    rid_range: tuple[int, int],
    throttle_ms: int = 150,
    verbose: bool = False,
    on_hit: Callable[[RoutineHit], None] | None = None,
) -> list[RoutineHit]:
    """Scan one ECU. Default session first, retry in extended on NRC 7F.

    Args:
        on_hit: Optional callback invoked after each hit is found. Use this
            to persist results incrementally so interrupted scans don't lose
            data.

    The caller is responsible for invoking this inside any broader session
    plumbing (we establish per-ECU extended session lazily on first 7F).
    """
    start, end = rid_range
    total = end - start + 1

    print(f"\n  [{ecu_name} @ 0x{tx_id:03X}] scanning RIDs 0x{start:04X}..0x{end:04X} ({total})")
    await terminal.set_header(tx_id)

    hits: list[RoutineHit] = []
    absent = 0
    errors = 0
    tester_task: asyncio.Task | None = None
    in_extended = False

    state = ScanStateWriter("routines", ecu_name, tx_id, start, end)
    state.open()
    try:
        for rid in range(start, end + 1):
            # Show current probe on stderr (overwritten each line)
            req_hex = f"31 03 {rid >> 8:02X} {rid & 0xFF:02X}"
            idx = rid - start + 1
            pct = idx / total * 100
            print(
                f"    [{idx}/{total} {pct:.0f}%] probing 0x{rid:04X}  ({req_hex})" + " " * 4,
                end="\r",
                file=sys.stderr,
            )

            response = await probe_routine(terminal, rid, SF_RESULTS)
            category, nrc = classify(response)

            if category == "wrong-session" and not in_extended:
                if verbose:
                    print(f"    0x{rid:04X}: NRC 7F — entering extended session")
                _, tester_task = await terminal.enter_extended_session(wake=False)
                in_extended = True
                # Retry in extended
                response = await probe_routine(terminal, rid, SF_RESULTS)
                category, nrc = classify(response)

            session_label = "extended" if in_extended else "default"

            if category == "positive":
                hit = RoutineHit(
                    rid=rid,
                    session=session_label,
                    response_hex=response.get("hex", ""),
                    nrc=None,
                    nrc_desc=None,
                )
                hits.append(hit)
                if on_hit:
                    on_hit(hit)
                resp_hex = response.get("hex", "")
                print(
                    f"    + 0x{rid:04X}: positive ({len(response.get('bytes', []))} bytes)"
                    + (f"  [{resp_hex}]" if resp_hex else "")
                )
            elif category == "exists":
                desc = response.get("nrc_desc", "")
                hit = RoutineHit(
                    rid=rid,
                    session=session_label,
                    response_hex="",
                    nrc=nrc,
                    nrc_desc=desc,
                )
                hits.append(hit)
                if on_hit:
                    on_hit(hit)
                print(f"    ~ 0x{rid:04X}: exists (NRC 0x{nrc:02X} {desc})")
            elif category == "absent":
                absent += 1
                if verbose:
                    print(f"    - 0x{rid:04X}: NRC 0x{nrc:02X}")
            else:
                errors += 1
                if verbose:
                    err = response.get("error", "unknown")
                    print(f"    ! 0x{rid:04X}: {err}")

            state.update(rid, hits=len(hits))

            if throttle_ms > 0:
                await asyncio.sleep(throttle_ms / 1000.0)
    finally:
        if tester_task:
            tester_task.cancel()
            try:
                await tester_task
            except asyncio.CancelledError:
                pass

    # Clear progress line
    print(" " * 60, end="\r", file=sys.stderr)
    print(f"  [{ecu_name}] done — {len(hits)} hits, {absent} absent, {errors} errors")
    state.close()
    return hits


async def mode_routines_scan(
    terminal: WiCANTerminal,
    pids_data: dict,
    ecus: list[str],
    rid_range: tuple[int, int] = (0xF000, 0xF0FF),
    throttle_ms: int = 150,
    verbose: bool = False,
    write_yaml: bool = True,
) -> dict[str, list[RoutineHit]]:
    """Scan RoutineControl IDs on one or more ECUs.

    Args:
        ecus: ECU names (must exist in ``pids_data``, case-insensitive).
        rid_range: (start, end) inclusive, 16-bit hex Routine IDs.
        throttle_ms: Sleep between probes to avoid hammering the bus.
        write_yaml: If True, merge hits into each ``pids/<ecu>.yaml`` under
            a new ``routines:`` section.

    Returns mapping of ECU name → list of hits.
    """
    # Build case-insensitive ECU → tx_id lookup
    ecu_defs: dict[str, dict] = {}
    for fname, fdata in pids_data.get("ecus", {}).items():
        if isinstance(fdata, dict):
            ecu_defs[fname.upper()] = fdata

    results: dict[str, list[RoutineHit]] = {}

    for ecu in ecus:
        key = ecu.upper()
        if key not in ecu_defs:
            print(f"  WARNING: ECU {ecu!r} not in pids_data, skipping", file=sys.stderr)
            continue
        tx_id = ecu_defs[key].get("tx_id")
        if tx_id is None:
            print(f"  WARNING: ECU {ecu!r} has no tx_id, skipping", file=sys.stderr)
            continue

        # Build incremental YAML writer callback so hits survive disconnects
        on_hit: Callable[[RoutineHit], None] | None = None
        if write_yaml:
            from ..pids_edit import append_routines_block

            _saved = 0

            def _write_hit(hit: RoutineHit, _ecu=key) -> None:
                nonlocal _saved
                try:
                    append_routines_block(_ecu, [hit])
                    _saved += 1
                except Exception as exc:
                    print(f"  [{_ecu}] ERROR writing YAML: {exc}", file=sys.stderr)

            on_hit = _write_hit

        hits = await scan_ecu(
            terminal,
            ecu_name=key,
            tx_id=tx_id,
            rid_range=rid_range,
            throttle_ms=throttle_ms,
            verbose=verbose,
            on_hit=on_hit,
        )
        results[key] = hits

        if write_yaml and hits:
            print(f"  [{key}] {len(hits)} routines saved to YAML (incremental)")

    # Summary
    print("\n  --- RoutineControl Scan Summary ---")
    for ecu_key, hit_list in results.items():
        print(f"    {ecu_key}: {len(hit_list)} routines found")

    return results
