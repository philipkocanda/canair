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

The generic scan loop lives in :mod:`canlib.modes.discovery_scan`; this module
supplies the 0x2F-specific probe, classification and writeback as a
:class:`DiscoveryProbe`.

Writeback
---------
Hits are written to ``ecus/<ecu>.yaml`` under a new top-level per-ECU section
``iocontrol_discoveries:`` — distinct from the curated ``iocontrol:`` block so
the scanner can rerun without clobbering human-authored on/off/notes entries.
"""

from __future__ import annotations

import sys
from typing import NamedTuple

from ..terminal import WiCANTerminal
from .discovery_scan import DiscoveryProbe, mode_discovery_scan

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


# Default DID ranges to scan per ECU (tuple of (start, end) pairs).
# Informed by the HKMC body-controller DID map:
#   B000-B07F  exterior lamps (head/tail/turn)
#   B080-B0FF  interior lamps + chimes
#   B100-B1FF  wipers, horn
#   B200-B2FF  door locks
#   B300-B3FF  windows, mirror
#   B400-B5FF  seats (PSM-owned)
#   B600-B7FF  steering-wheel heater
#   BC00-BCFF  EV accessory (IGPM-owned)
#   BD00-BDFF  extended EV accessory
#   C000-C0FF  TPMS
#   F000-F2FF  HVAC climate
# Curated hits so far cluster in these zones; the broader B800-BBFF /
# B800-BFFF gaps are empty in practice so we skip them.
DEFAULT_ECU_RANGES: dict[str, list[tuple[int, int]]] = {
    "IGPM": [(0xB000, 0xBFFF), (0xBD00, 0xBDFF), (0xC000, 0xC0FF)],
    "BCM": [(0xB000, 0xB3FF), (0xB400, 0xB7FF), (0xC000, 0xC0FF), (0xF000, 0xF0FF)],
    "HVAC": [(0xF000, 0xFFFF)],
    "PSM": [(0xB000, 0xBFFF)],
}


def _make_hit(did, session, response_hex, nrc, nrc_desc) -> IOControlHit:
    return IOControlHit(did, session, response_hex, nrc, nrc_desc)


def _write_hit(ecu_name: str, hit: IOControlHit) -> None:
    from ..pids_edit import append_iocontrol_discoveries_block

    try:
        append_iocontrol_discoveries_block(ecu_name, [hit])
    except Exception as exc:
        print(f"  [{ecu_name}] ERROR writing YAML: {exc}", file=sys.stderr)


IOCONTROL_PROBE = DiscoveryProbe(
    name="IOControl (0x2F)",
    scan_type="iocontrol",
    id_label="DID",
    id_width=2,
    service=0x2F,
    probe=probe_iocontrol,
    classify=classify,
    make_hit=_make_hit,
    request_display=lambda did: f"2F {did >> 8:02X} {did & 0xFF:02X} 00",
    write_hit=_write_hit,
)


async def mode_iocontrol_scan(
    terminal: WiCANTerminal,
    pids_data: dict,
    ecus: list[str],
    did_range: tuple[int, int] | None = None,
    throttle_ms: int = 150,
    verbose: bool = False,
    write_yaml: bool = True,
    session: bool = False,
    wake: bool = False,
    session_mode: str = "03",
) -> dict[str, list[IOControlHit]]:
    """Scan IOControl DIDs on one or more ECUs using SF 00 returnControlToECU.

    Thin wrapper over :func:`discovery_scan.mode_discovery_scan` with the 0x2F
    probe. Uses :data:`DEFAULT_ECU_RANGES` per ECU when ``did_range`` is None.
    """
    return await mode_discovery_scan(
        terminal,
        IOCONTROL_PROBE,
        pids_data,
        ecus=ecus,
        id_range=did_range,
        default_ranges=DEFAULT_ECU_RANGES,
        default_range=(0xB000, 0xBFFF),
        throttle_ms=throttle_ms,
        verbose=verbose,
        write_yaml=write_yaml,
        session=session,
        wake=wake,
        session_mode=session_mode,
    )
