"""IOControl (UDS 0x2F) DID discovery scanner.

Probes ``2F {DID_HI} {DID_LO} 00`` (sub-function ``returnControlToECU``)
across a range of 16-bit Data Identifiers to enumerate which DIDs on a
given ECU support InputOutputControl.

Safety
------
Sub-function ``0x00 returnControlToECU`` is side-effect-free by ISO 14229:
it releases control back to the ECU and does **not** drive any actuator.
If nothing was under control, the ECU returns the current signal value.

NEVER use this scanner with any other sub-function:
  * 0x01 resetToDefault         — resets the signal; may move things
  * 0x02 freezeCurrentState     — locks current output (shift register)
  * 0x03 shortTermAdjustment    — actually drives the actuator → dangerous

Classification
--------------
  positive ``6F {DID} 00 ...`` — DID exists AND supports IOControl
  NRC 0x31 (requestOutOfRange) — DID is not an IOControl target → absent
  NRC 0x22 (conditionsNotCorrect) — exists, preconditions unmet → hit
  NRC 0x33 (securityAccessDenied) — exists, gated by 0x27 → hit
  NRC 0x12 (subFunctionNotSupported) — exists but doesn't accept SF 00 → hit
  NRC 0x7F (serviceNotSupportedInActiveSession) — retry in extended session
  NRC 0x11 (serviceNotSupported) — ECU doesn't implement 0x2F at all → abort

Writeback
---------
Hits are written to ``pids/<ecu>.yaml`` under a new top-level per-ECU section
``iocontrol_discoveries:`` — distinct from the curated ``iocontrol:`` block so
the scanner can rerun without clobbering human-authored on/off/notes entries.
"""

from __future__ import annotations

import asyncio
import sys
from typing import NamedTuple

from ..terminal import WiCANTerminal

# IOControl sub-functions — 0x00 is the ONLY safe one for scanning
SF_RETURN_CONTROL = 0x00  # returnControlToECU — safe
SF_RESET_TO_DEFAULT = 0x01  # DO NOT USE
SF_FREEZE = 0x02  # DO NOT USE
SF_SHORT_TERM_ADJ = 0x03  # DO NOT USE (actuates!)

# NRCs that still indicate "DID exists and supports IOControl"
NRC_EXISTS_HINTS = {
    0x22,  # conditionsNotCorrect — present, preconditions unmet
    0x33,  # securityAccessDenied — present, locked
    0x72,  # generalProgrammingFailure — present, error state
    0x78,  # requestCorrectlyReceivedResponsePending
}

# NRCs that indicate "absent or unsupported"
NRC_ABSENT = {
    0x11,  # serviceNotSupported (whole ECU — caller should abort)
    0x31,  # requestOutOfRange — DID not an IOControl target
}

# NRC that might mean "DID exists but wants a different sub-function" — treat as hit
NRC_SF_UNSUPPORTED = 0x12

# NRC that suggests retrying in extended session
NRC_WRONG_SESSION = 0x7F


class IOControlHit(NamedTuple):
    """A single positive IOControl probe result."""

    did: int
    session: str  # "default" or "extended"
    response_hex: str  # empty if NRC-based hit
    nrc: int | None  # None for positive response
    nrc_desc: str | None


async def probe_iocontrol(
    terminal: WiCANTerminal,
    did: int,
    sub_function: int = SF_RETURN_CONTROL,
) -> dict:
    """Send ``2F {DID_HI} {DID_LO} {SF}`` and return the parsed response.

    Uses SID/DID echo validation (``expected_sid=0x2F``, ``expected_did=did``)
    so that stale or misaligned frames buffered in the ELM327 adapter are
    caught and reported as errors rather than misattributed to the wrong
    DID. Observed failure mode: a late-arriving ``6F {prev_did} 00`` from
    the previous probe leaks into the next read, silently shifting every
    subsequent hit by -1 DID. See `tests/test_iocontrol_scan.py`.
    """
    if sub_function != SF_RETURN_CONTROL:
        raise ValueError(
            f"Refusing to probe with sub-function 0x{sub_function:02X}; "
            f"only 0x00 returnControlToECU is safe for scanning."
        )
    req = f"2F{did:04X}{sub_function:02X}"
    return await terminal.send_uds(
        req,
        timeout=2.0,
        expected_sid=0x2F,
        expected_did=did,
    )


def classify(response: dict) -> tuple[str, int | None]:
    """Classify a probe response.

    Returns (category, nrc) where category is one of:
        "positive"       — ECU answered 6F ...
        "exists"         — NRC indicates the DID is present (22/33/72/78/12)
        "absent"         — NRC 0x31 (requestOutOfRange)
        "service-absent" — NRC 0x11 (whole ECU doesn't do 0x2F — abort scan)
        "wrong-session"  — NRC 0x7F, retry in extended
        "error"          — transport/timeout/other
    """
    if response.get("ok"):
        return "positive", None
    nrc = response.get("nrc")
    if nrc is None:
        return "error", None
    if nrc == NRC_WRONG_SESSION:
        return "wrong-session", nrc
    if nrc == 0x11:
        return "service-absent", nrc
    if nrc == 0x31:
        return "absent", nrc
    if nrc == NRC_SF_UNSUPPORTED:
        return "exists", nrc
    if nrc in NRC_EXISTS_HINTS:
        return "exists", nrc
    # Unknown NRC — conservatively treat as a hit so we don't miss anything
    return "exists", nrc


async def scan_ecu(
    terminal: WiCANTerminal,
    ecu_name: str,
    tx_id: int,
    did_range: tuple[int, int],
    throttle_ms: int = 150,
    verbose: bool = False,
) -> list[IOControlHit]:
    """Scan one ECU over ``did_range`` inclusive. Auto-upgrades to extended
    session on first NRC 0x7F.
    """
    start, end = did_range
    total = end - start + 1

    print(f"\n  [{ecu_name} @ 0x{tx_id:03X}] scanning DIDs 0x{start:04X}..0x{end:04X} ({total})")
    await terminal.set_header(tx_id)

    hits: list[IOControlHit] = []
    absent = 0
    errors = 0
    tester_task: asyncio.Task | None = None
    in_extended = False

    try:
        for did in range(start, end + 1):
            response = await probe_iocontrol(terminal, did, SF_RETURN_CONTROL)
            category, nrc = classify(response)

            if category == "service-absent":
                print(f"  [{ecu_name}] NRC 0x11 (serviceNotSupported) — "
                      f"ECU doesn't implement 0x2F, aborting scan")
                break

            if category == "wrong-session" and not in_extended:
                if verbose:
                    print(f"    0x{did:04X}: NRC 7F — entering extended session")
                _, tester_task = await terminal.enter_extended_session(wake=False)
                in_extended = True
                response = await probe_iocontrol(terminal, did, SF_RETURN_CONTROL)
                category, nrc = classify(response)

            session_label = "extended" if in_extended else "default"

            if category == "positive":
                hit = IOControlHit(
                    did=did,
                    session=session_label,
                    response_hex=response.get("hex", ""),
                    nrc=None,
                    nrc_desc=None,
                )
                hits.append(hit)
                nbytes = len(response.get("bytes", []))
                print(f"    + 0x{did:04X}: positive ({nbytes} bytes)")
            elif category == "exists":
                desc = response.get("nrc_desc", "")
                hit = IOControlHit(
                    did=did,
                    session=session_label,
                    response_hex="",
                    nrc=nrc,
                    nrc_desc=desc,
                )
                hits.append(hit)
                print(f"    ~ 0x{did:04X}: exists (NRC 0x{nrc:02X} {desc})")
            elif category == "absent":
                absent += 1
                if verbose:
                    print(f"    - 0x{did:04X}: NRC 0x{nrc:02X}")
            else:
                errors += 1
                if verbose:
                    err = response.get("error", "unknown")
                    print(f"    ! 0x{did:04X}: {err}")

            if not verbose and category == "absent":
                idx = did - start + 1
                if idx % 128 == 0:
                    pct = idx / total * 100
                    print(f"    ... {idx}/{total} ({pct:.0f}%)",
                          end="\r", file=sys.stderr)

            if throttle_ms > 0:
                await asyncio.sleep(throttle_ms / 1000.0)
    finally:
        if tester_task:
            tester_task.cancel()
            try:
                await tester_task
            except asyncio.CancelledError:
                pass

    print(f"  [{ecu_name}] done — {len(hits)} hits, {absent} absent, {errors} errors")
    return hits


# Default DID ranges to scan per ECU (tuple of (start, end) pairs).
# Based on HKMC convention: body actuators live in B0-BF, comfort in F0-FF.
DEFAULT_ECU_RANGES: dict[str, list[tuple[int, int]]] = {
    "IGPM": [(0xB000, 0xBFFF)],
    "BCM": [(0xB000, 0xCFFF)],
    "HVAC": [(0xF000, 0xFFFF)],
    "PSM": [(0xB000, 0xB5FF)],
}


async def mode_iocontrol_scan(
    terminal: WiCANTerminal,
    pids_data: dict,
    ecus: list[str],
    did_range: tuple[int, int] | None = None,
    throttle_ms: int = 150,
    verbose: bool = False,
    write_yaml: bool = True,
) -> dict[str, list[IOControlHit]]:
    """Scan IOControl DIDs on one or more ECUs using SF 00 returnControlToECU.

    Args:
        ecus: ECU names (case-insensitive, must exist in pids_data).
        did_range: (start, end) inclusive. If None, uses DEFAULT_ECU_RANGES
            per ECU (B0-BF for body ECUs, F0-FF for HVAC).
        throttle_ms: Delay between probes.
        write_yaml: If True, merges hits into pids/<ecu>.yaml under the
            ``iocontrol_discoveries:`` section.

    Returns mapping of ECU name → list of hits.
    """
    ecu_defs: dict[str, dict] = {}
    for fname, fdata in pids_data.get("ecus", {}).items():
        if isinstance(fdata, dict):
            ecu_defs[fname.upper()] = fdata

    results: dict[str, list[IOControlHit]] = {}

    for ecu in ecus:
        key = ecu.upper()
        if key not in ecu_defs:
            print(f"  WARNING: ECU {ecu!r} not in pids_data, skipping", file=sys.stderr)
            continue
        tx_id = ecu_defs[key].get("tx_id")
        if tx_id is None:
            print(f"  WARNING: ECU {ecu!r} has no tx_id, skipping", file=sys.stderr)
            continue

        if did_range is not None:
            ranges = [did_range]
        else:
            ranges = DEFAULT_ECU_RANGES.get(key, [(0xB000, 0xBFFF)])

        ecu_hits: list[IOControlHit] = []
        for rng in ranges:
            hits = await scan_ecu(
                terminal,
                ecu_name=key,
                tx_id=tx_id,
                did_range=rng,
                throttle_ms=throttle_ms,
                verbose=verbose,
            )
            ecu_hits.extend(hits)

        results[key] = ecu_hits

        if write_yaml and ecu_hits:
            from ..pids_edit import append_iocontrol_discoveries_block

            try:
                path = append_iocontrol_discoveries_block(key, ecu_hits)
                print(f"  [{key}] wrote {len(ecu_hits)} discoveries to {path.name}")
            except Exception as exc:
                print(f"  [{key}] ERROR writing YAML: {exc}", file=sys.stderr)

    print("\n  --- IOControl Discovery Scan Summary ---")
    for ecu_key, hit_list in results.items():
        positive = sum(1 for h in hit_list if h.nrc is None)
        nrc_hits = len(hit_list) - positive
        print(f"    {ecu_key}: {len(hit_list)} hits ({positive} positive, {nrc_hits} NRC)")

    return results
