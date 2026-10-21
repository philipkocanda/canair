"""KWP2000 IOControl (0x30) local-identifier discovery scanner.

KWP2000 (ISO 14230-3) actuator tests use **InputOutputControlByLocalIdentifier**
— service ``0x30`` — which is distinct from UDS ``0x2F``
InputOutputControlByIdentifier:

    UDS 0x2F : 2F {DID_HI} {DID_LO} {IOCP}   -- 16-bit Data Identifier
    KWP 0x30 : 30 {LID}       {IOCP} [state] -- 8-bit Local Identifier

The Ioniq's powertrain ECUs (BMS, VCU, MCU, LDC, AAF) speak KWP2000, so they
answer UDS ``0x2F`` with ``NRC 0x11 serviceNotSupported`` — the ``0x2F`` scanner
cannot reach them. This scanner probes ``0x30`` instead, enumerating which local
identifiers accept IOControl (e.g. the air-cooled 28 kWh pack's battery fan).

Safety
------
The InputOutputControlParameter (IOCP) byte immediately follows the LID. Value
``0x00`` = ``returnControlToECU`` is side-effect-free: it hands control back to
the ECU and drives nothing (the KWP2000 analogue of UDS SF 0x00, and matching
HKMC's own ``…00`` = release / ``…03`` = actuate convention). In this layout the
scanner sends ONLY ``30 {LID} 00`` and hard-refuses any other IOCP.

NEVER scan the ISO layout with a non-zero IOCP — e.g. ``0x03
shortTermAdjustment`` actually drives the actuator.

Positive response is ``0x70``. Classification mirrors the UDS 0x2F scanner.
The generic scan loop lives in :mod:`canlib.modes.discovery_scan`.

Nissan
------
Nissan ECUs do not use the ISO 14230-3 layout: their request is
``30 {LID} {function} {value}``, and function ``0x00`` is a *one-shot command*,
so ``30 {LID} 00`` is the start of an actuation rather than a release. For a
profile with the ``nissan_c0_session`` quirk the scanner therefore sends
``30 {LID} 01 00`` instead — function ``0x01`` only asks whether value ``00`` is
supported and drives nothing — inside the Nissan session ``10 C0``, which those
ECUs require for service ``0x30``.
"""

from __future__ import annotations

import dataclasses
import sys
from typing import NamedTuple

from ..quirks import NISSAN_C0_SESSION, has_quirk
from ..transport.protocol import Terminal
from ..uds_parse import UdsResponse
from .discovery_scan import DiscoveryProbe, mode_discovery_scan

# InputOutputControlParameter (IOCP) — byte after the LID. 0x00 is the ONLY safe one.
IOCP_RETURN_CONTROL = 0x00  # returnControlToECU — safe
IOCP_RESET_TO_DEFAULT = 0x01  # DO NOT USE
IOCP_FREEZE = 0x02  # DO NOT USE
IOCP_SHORT_TERM_ADJ = 0x03  # DO NOT USE (actuates!)

# Nissan layout (30 {LID} {function} {value}): 0x01 asks whether {value} is
# supported. 0x00 (one-shot command) and 0x20 (timed change) both actuate.
NISSAN_FN_QUERY_SUPPORTED = 0x01
NISSAN_SESSION_MODE = "C0"
DEFAULT_SESSION_MODE = "03"

# Reuse the exact NRC semantics of the UDS 0x2F scanner — they are protocol-shared.
NRC_EXISTS_HINTS = {
    0x22,  # conditionsNotCorrect — present, preconditions unmet
    0x33,  # securityAccessDenied — present, locked
    0x72,  # generalProgrammingFailure — present, error state
    0x78,  # requestCorrectlyReceivedResponsePending
}
NRC_ABSENT = {
    0x11,  # serviceNotSupported (whole ECU — abort)
    0x31,  # requestOutOfRange — LID not an IOControl target
}
NRC_SF_UNSUPPORTED = 0x12
NRC_WRONG_SESSION = 0x7F
# KWP2000's serviceNotSupportedInActiveDiagnosticMode — what a Nissan ECU answers
# outside 10 C0. Read as "exists", it would record every LID as a hit.
NRC_KWP_WRONG_SESSION = 0x80


class KwpIOControlHit(NamedTuple):
    """A single positive KWP2000 IOControl probe result.

    ``did`` names the 8-bit local identifier (kept as ``did`` so the shared
    ``iocontrol_discoveries:`` writeback and hit helpers work unchanged).
    """

    did: int
    session: str  # "default" or "extended"
    response_hex: str  # empty if NRC-based hit
    nrc: int | None  # None for positive response
    nrc_desc: str | None


async def probe_kwp_iocontrol(
    terminal: Terminal,
    lid: int,
    iocp: int = IOCP_RETURN_CONTROL,
) -> UdsResponse:
    """Send ``30 {LID} 00`` (returnControlToECU) and return the parsed response.

    Validates the ``0x70`` response SID echo. Refuses any IOCP other than
    ``0x00`` — the scanner must never actuate.
    """
    if iocp != IOCP_RETURN_CONTROL:
        raise ValueError(
            f"Refusing to probe with IOCP 0x{iocp:02X}; "
            f"only 0x00 returnControlToECU is safe for scanning."
        )
    return await _send_probe(terminal, lid, f"{iocp:02X}")


async def probe_nissan_iocontrol(terminal: Terminal, lid: int) -> UdsResponse:
    """Send Nissan's ``30 {LID} 01 00`` (is value 00 supported?) and return the response.

    Value ``00`` is the one that means "nothing" or "off" in Nissan's other
    functions, so even an ECU that misread the function byte would not actuate.
    """
    return await _send_probe(terminal, lid, f"{NISSAN_FN_QUERY_SUPPORTED:02X}00")


async def _send_probe(terminal: Terminal, lid: int, tail: str) -> UdsResponse:
    if not 0 <= lid <= 0xFF:
        raise ValueError(f"KWP2000 local identifier must be a single byte, got 0x{lid:X}")
    resp = await terminal.send_uds(f"30{lid:02X}{tail}", timeout=2.0, expected_sid=0x30)
    # The LID echoes in response byte 1 (70 {LID} ...). Guard against the
    # stale-frame -1 shift seen on the 0x2F scanner: reject an echo mismatch.
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
    """Classify a probe response (identical semantics to the UDS 0x2F scanner).

    "positive" | "exists" | "absent" | "service-absent" | "wrong-session" | "error".
    """
    if response.get("ok"):
        return "positive", None
    nrc = response.get("nrc")
    if nrc is None:
        return "error", None
    if nrc in (NRC_WRONG_SESSION, NRC_KWP_WRONG_SESSION):
        return "wrong-session", nrc
    if nrc == 0x11:
        return "service-absent", nrc
    if nrc == 0x31:
        return "absent", nrc
    if nrc == NRC_SF_UNSUPPORTED:
        return "exists", nrc
    if nrc in NRC_EXISTS_HINTS:
        return "exists", nrc
    return "exists", nrc


def _make_hit(lid, session, response_hex, nrc, nrc_desc) -> KwpIOControlHit:
    return KwpIOControlHit(lid, session, response_hex, nrc, nrc_desc)


def _write_hit(ecu_name: str, hit: KwpIOControlHit) -> None:
    from ..pids_edit import append_iocontrol_discoveries_block

    try:
        # KWP local identifiers are a single byte → 2-hex-digit keys.
        append_iocontrol_discoveries_block(ecu_name, [hit], key_width=2)
    except Exception as exc:
        print(f"  [{ecu_name}] ERROR writing YAML: {exc}", file=sys.stderr)


KWP_IOCONTROL_PROBE = DiscoveryProbe(
    name="KWP2000 IOControl (0x30)",
    scan_type="iocontrol-kwp",
    id_label="LID",
    id_width=1,
    service=0x30,
    probe=probe_kwp_iocontrol,
    classify=classify,
    make_hit=_make_hit,
    request_display=lambda lid: f"30 {lid:02X} 00",
    write_hit=_write_hit,
)

NISSAN_IOCONTROL_PROBE = dataclasses.replace(
    KWP_IOCONTROL_PROBE,
    name="Nissan IOControl (0x30, function 01)",
    probe=probe_nissan_iocontrol,
    request_display=lambda lid: f"30 {lid:02X} 01 00",
)


async def mode_kwp_iocontrol_scan(
    terminal: Terminal,
    pids_data: dict,
    ecus: list[str],
    lid_range: tuple[int, int] | None = None,
    throttle_ms: int = 150,
    verbose: bool = False,
    write_yaml: bool = True,
    session: bool = False,
    wake: bool = False,
    session_mode: str | None = None,
) -> dict[str, list[KwpIOControlHit]]:
    """Scan KWP2000 IOControl local identifiers with a probe that drives nothing.

    Thin wrapper over :func:`discovery_scan.mode_discovery_scan`. Default LID
    range is the full single-byte space ``00–FF``. A Nissan profile gets the
    Nissan probe inside ``10 C0`` (``session_mode`` still overrides the session).
    """
    probe = KWP_IOCONTROL_PROBE
    if has_quirk(pids_data, NISSAN_C0_SESSION):
        probe = NISSAN_IOCONTROL_PROBE
        session = True
        session_mode = session_mode or NISSAN_SESSION_MODE
    return await mode_discovery_scan(
        terminal,
        probe,
        pids_data,
        ecus=ecus,
        id_range=lid_range,
        default_range=(0x00, 0xFF),
        throttle_ms=throttle_ms,
        verbose=verbose,
        write_yaml=write_yaml,
        session=session,
        wake=wake,
        session_mode=session_mode or DEFAULT_SESSION_MODE,
    )
