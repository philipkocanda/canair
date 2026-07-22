"""Diagnostic session-type discovery scanner (DiagnosticSessionControl, 0x10).

Probes which diagnostic *session types* an ECU supports by sending
``10 {sub-function}`` and classifying the reply:

    positive (50 xx …)               -- session type supported
    NRC 0x12 subFunctionNotSupported -- session type not implemented
    NRC 0x11 serviceNotSupported     -- ECU doesn't implement 0x10 at all → abort
    NRC 0x7F …InActiveSession        -- rejected in the current session

This is a read-only probe: entering a diagnostic session has no side effects and
the ECU auto-reverts to the default session once TesterPresent stops. Only the
SAFE session modes are probed — the programming sessions (UDS ``0x02`` and
KWP2000 ``0x85``) are NEVER sent, matching the guard in :mod:`canlib.safety`.

Results are written to each ``ecus/<ecu>.yaml`` under a structured ``sessions:``
section (see the schema's ``sessions_fields``), replacing the free-text session
notes that used to live in top-of-file comments.

Session modes are protocol-specific and auto-selected from each ECU's
``id_protocol`` (UDS vs KWP2000).
"""

from __future__ import annotations

import asyncio
import sys
from typing import NamedTuple

from ..terminal import WiCANTerminal

# Human labels for the session sub-functions we know about.
SESSION_NAMES = {
    0x01: "defaultSession",
    0x03: "extendedDiagnosticSession",
    0x81: "standardDiagnosticSession",
    0x82: "periodicDiagnosticSession",
    0x83: "extendedDiagnosticSession",
}

# Safe session sub-functions to probe, per protocol. Programming sessions
# (UDS 0x02, KWP2000 0x85) are deliberately excluded — they are blocked by
# canlib.safety and must never be sent to this car.
UDS_SESSION_MODES = (0x01, 0x03)
KWP_SESSION_MODES = (0x81, 0x82, 0x83)

# NRC that means "this ECU doesn't implement DiagnosticSessionControl" → abort.
NRC_SERVICE_ABSENT = 0x11
# NRC that means the session type simply isn't implemented (still informative).
NRC_NOT_SUPPORTED = 0x12
# NRC that suggests the request was rejected in the current session.
NRC_WRONG_SESSION = 0x7F


class SessionHit(NamedTuple):
    """A single session-type probe result."""

    mode: int  # 0x10 sub-function (e.g. 0x03, 0x81)
    name: str | None  # human label, if known
    supported: bool  # True → 50 xx positive response
    nrc: int | None  # negative response code (None if supported)
    nrc_desc: str | None


def classify(response: dict) -> tuple[str, int | None]:
    """Classify a ``10 {mode}`` probe response.

    Returns (category, nrc) where category is one of:
        "supported"       — ECU answered 50 xx
        "not-supported"   — NRC indicates the session type is absent
        "service-absent"  — NRC 0x11, ECU doesn't implement 0x10 → abort
        "wrong-session"   — NRC 0x7F, rejected in the active session
        "error"           — transport/timeout/other
    """
    if response.get("ok"):
        return "supported", None
    nrc = response.get("nrc")
    if nrc is None:
        return "error", None
    if nrc == NRC_SERVICE_ABSENT:
        return "service-absent", nrc
    if nrc == NRC_WRONG_SESSION:
        return "wrong-session", nrc
    # Everything else (0x12 subFunctionNotSupported, 0x22, 0x31, …) means the
    # ECU understood the service but not this session type.
    return "not-supported", nrc


async def probe_session(terminal: WiCANTerminal, mode: int) -> dict:
    """Send ``10 {mode}`` and return the parsed response (SID echo validated)."""
    req = f"10{mode:02X}"
    return await terminal.send_uds(req, timeout=3.0, expected_sid=0x10)


def _write_hit(ecu_name: str, hits: list[SessionHit]) -> None:
    from ..pids_edit import append_sessions_block

    try:
        append_sessions_block(ecu_name, hits)
    except Exception as exc:  # pragma: no cover - defensive I/O guard
        print(f"  [{ecu_name}] ERROR writing YAML: {exc}", file=sys.stderr)


def _modes_for_protocol(proto: str | None) -> tuple[int, ...]:
    """Pick the safe session-mode set for an ECU's ``id_protocol``."""
    if str(proto or "").upper().startswith("KWP"):
        return KWP_SESSION_MODES
    return UDS_SESSION_MODES


async def scan_ecu_sessions(
    terminal: WiCANTerminal,
    ecu_name: str,
    tx_id: int,
    modes: tuple[int, ...],
    throttle_ms: int = 200,
    verbose: bool = False,
    write_yaml: bool = True,
) -> list[SessionHit]:
    """Probe each session ``mode`` on one ECU and return the hits.

    Aborts early with an empty result if the ECU returns NRC 0x11
    (serviceNotSupported) — it doesn't implement DiagnosticSessionControl.
    """
    print(
        f"\n  [{ecu_name} @ 0x{tx_id:03X}] DiagnosticSessionControl (0x10): probing "
        f"{len(modes)} session type(s): {', '.join(f'0x{m:02X}' for m in modes)}"
    )
    await terminal.set_header(tx_id)

    hits: list[SessionHit] = []
    for mode in modes:
        response = await probe_session(terminal, mode)
        category, nrc = classify(response)
        name = SESSION_NAMES.get(mode)

        if category == "service-absent":
            print(
                f"  [{ecu_name}] NRC 0x{nrc:02X} — ECU doesn't implement "
                f"service 0x10 (DiagnosticSessionControl), aborting"
            )
            hits = []
            break

        if category == "supported":
            hit = SessionHit(mode=mode, name=name, supported=True, nrc=None, nrc_desc=None)
            hits.append(hit)
            print(f"    + 10 {mode:02X}: supported" + (f" ({name})" if name else ""))
        elif category == "wrong-session":
            # Rejected in the current session — record as unsupported-in-context.
            desc = response.get("nrc_desc", "")
            hit = SessionHit(mode=mode, name=name, supported=False, nrc=nrc, nrc_desc=desc)
            hits.append(hit)
            print(f"    ~ 10 {mode:02X}: NRC 0x{nrc:02X} {desc} (rejected in active session)")
        elif category == "not-supported":
            desc = response.get("nrc_desc", "")
            hit = SessionHit(mode=mode, name=name, supported=False, nrc=nrc, nrc_desc=desc)
            hits.append(hit)
            print(f"    - 10 {mode:02X}: not supported (NRC 0x{nrc:02X} {desc})")
        else:
            if verbose:
                print(f"    ! 10 {mode:02X}: {response.get('error', 'unknown')}")

        if throttle_ms > 0:
            await asyncio.sleep(throttle_ms / 1000.0)

    if write_yaml and hits:
        _write_hit(ecu_name, hits)

    supported = sum(1 for h in hits if h.supported)
    print(f"  [{ecu_name}] done — {supported}/{len(hits)} session type(s) supported")
    return hits


async def mode_sessions_scan(
    terminal: WiCANTerminal,
    pids_data: dict,
    ecus: list[str],
    modes: tuple[int, ...] | None = None,
    throttle_ms: int = 200,
    verbose: bool = False,
    write_yaml: bool = True,
) -> dict[str, list[SessionHit]]:
    """Probe the supported diagnostic session types on one or more ECUs.

    For each ECU the session-mode set is auto-selected from its ``id_protocol``
    (UDS → 01/03; KWP2000 → 81/82/83) unless an explicit ``modes`` tuple is
    given. Only safe read-only session modes are ever sent. Hits are written to
    each ``ecus/<ecu>.yaml`` under a ``sessions:`` section.

    Returns mapping of ECU name (upper-case) → list of :class:`SessionHit`.
    """
    ecu_defs: dict[str, dict] = {}
    for fname, fdata in pids_data.get("ecus", {}).items():
        if isinstance(fdata, dict):
            ecu_defs[fname.upper()] = fdata

    results: dict[str, list[SessionHit]] = {}
    for ecu in ecus:
        key = ecu.upper()
        if key not in ecu_defs:
            print(f"  WARNING: ECU {ecu!r} not in pids_data, skipping", file=sys.stderr)
            continue
        tx_id = ecu_defs[key].get("tx_id")
        if tx_id is None:
            print(f"  WARNING: ECU {ecu!r} has no tx_id, skipping", file=sys.stderr)
            continue

        if modes is not None:
            ecu_modes = modes
        else:
            proto = (ecu_defs[key].get("identity") or {}).get("id_protocol")
            ecu_modes = _modes_for_protocol(proto)

        results[key] = await scan_ecu_sessions(
            terminal,
            ecu_name=key,
            tx_id=tx_id,
            modes=ecu_modes,
            throttle_ms=throttle_ms,
            verbose=verbose,
            write_yaml=write_yaml,
        )

    print("\n  --- Diagnostic Session Type Scan Summary ---")
    for ecu_key, hit_list in results.items():
        supported = sum(1 for h in hit_list if h.supported)
        print(f"    {ecu_key}: {supported} supported, {len(hit_list) - supported} unsupported")

    return results
