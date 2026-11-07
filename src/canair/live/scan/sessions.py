"""Diagnostic session-type discovery scanner (DiagnosticSessionControl, 0x10).

Probes which diagnostic *session types* an ECU supports by sending
``10 {sub-function}`` and classifying the reply:

    positive (50 xx …)               -- session type supported
    NRC 0x12 subFunctionNotSupported -- session type not implemented
    NRC 0x11 serviceNotSupported     -- ECU doesn't implement 0x10 at all → abort
    NRC 0x7E/0x7F …InActiveSession  -- exists, but not from the current session
    (KWP2000 0x80)

This is a read-only probe: entering a diagnostic session has no side effects and
the ECU auto-reverts to the default session once TesterPresent stops. Only the
SAFE session modes are probed — the programming sessions (UDS ``0x02``, also as
``0x82`` with the suppress bit, and KWP2000 ``0x85``) are NEVER sent: every mode is
checked against :mod:`canair.safety.blocklist` for the ECU's protocol before the sweep.

Results are written to each ``ecus/<ecu>.yaml`` under a structured ``sessions:``
section (the ECU schema's ``session_entry``), replacing the free-text session
notes that used to live in top-of-file comments.

Session modes are protocol-specific and auto-selected from each ECU's
``id_protocol`` (UDS vs KWP2000).
"""

from __future__ import annotations

import asyncio
import sys
from typing import NamedTuple

from canair.profile.model import Profile
from canair.render import messages
from canair.render.paint import Painter
from canair.safety.blocklist import SessionPolicy
from canair.safety.quirks import allowed_session_modes
from canair.transport.protocol import Terminal
from canair.uds.nrc import (
    NOT_IN_ACTIVE_SESSION_NRCS,
    NRC_SERVICE_NOT_SUPPORTED,
    NRC_SUB_FUNCTION_NOT_SUPPORTED_IN_ACTIVE_SESSION,
)
from canair.uds.protocol import KWP, classify_protocol
from canair.uds.requests import session_request
from canair.uds.response import UdsResponse
from canair.uds.services import DIAGNOSTIC_SESSION_CONTROL
from canair.uds.subfunctions import SUBFUNCTION_NAMES, KwpSessionType, SessionType
from canair.yaml_edit.pids import append_sessions_block

from ...render.discover import ABSENT, POSITIVE, PROBE_FAILED, REFUSED

# Human labels for the session sub-functions, from the shared table in
# canair.uds.subfunctions — the single home for these enums. Keeping a private copy here
# is what let it drift out of step with formatting.py's (which was missing the
# whole KWP2000 0x8x range).
SESSION_NAMES = SUBFUNCTION_NAMES[DIAGNOSTIC_SESSION_CONTROL]

# Safe session sub-functions to probe, per protocol. Programming sessions
# (UDS 0x02, KWP2000 0x85) are deliberately excluded — they are blocked by
# canair.safety.blocklist and must never be sent to this car.
UDS_SESSION_MODES = (SessionType.DEFAULT_SESSION, SessionType.EXTENDED_DIAGNOSTIC_SESSION)
KWP_SESSION_MODES = (
    KwpSessionType.STANDARD_DIAGNOSTIC_SESSION,
    KwpSessionType.PERIODIC_DIAGNOSTIC_SESSION,
    KwpSessionType.EXTENDED_DIAGNOSTIC_SESSION,
)


# A session type the ECU has but will not enter from the active session. Read as "not
# supported", it would be recorded as absent although another session can reach it.
_WRONG_SESSION_NRCS = NOT_IN_ACTIVE_SESSION_NRCS | {
    NRC_SUB_FUNCTION_NOT_SUPPORTED_IN_ACTIVE_SESSION
}


class SessionHit(NamedTuple):
    """A single session-type probe result."""

    mode: int  # 0x10 sub-function (e.g. 0x03, 0x81)
    name: str | None  # human label, if known
    supported: bool  # True → 50 xx positive response
    nrc: int | None  # negative response code (None if supported)
    nrc_desc: str | None


def classify(response: UdsResponse) -> tuple[str, int | None]:
    """Classify a ``10 {mode}`` probe response.

    Returns (category, nrc) where category is one of:
        "supported"       — ECU answered 50 xx
        "not-supported"   — NRC indicates the session type is absent
        "service-absent"  — NRC 0x11, ECU doesn't implement 0x10 → abort
        "wrong-session"   — rejected in the active session: UDS 0x7E (the session type
                            exists but not from here), 0x7F, or KWP2000's 0x80
        "error"           — transport/timeout/other
    """
    if response.get("ok"):
        return "supported", None
    nrc = response.get("nrc")
    if nrc is None:
        return "error", None
    if nrc == NRC_SERVICE_NOT_SUPPORTED:
        return "service-absent", nrc
    if nrc in _WRONG_SESSION_NRCS:
        return "wrong-session", nrc
    # Everything else (0x12 subFunctionNotSupported, 0x22, 0x31, …) means the
    # ECU understood the service but not this session type.
    return "not-supported", nrc


async def probe_session(terminal: Terminal, mode: int) -> UdsResponse:
    """Send ``10 {mode}`` and return the parsed response (SID echo validated)."""
    req = session_request(mode)
    return await terminal.send_uds(req, timeout=3.0, expected_sid=DIAGNOSTIC_SESSION_CONTROL)


def _write_hit(ecu_name: str, hits: list[SessionHit], *, profile: Profile) -> None:
    try:
        append_sessions_block(ecu_name, hits, profile=profile)
    except Exception as exc:  # pragma: no cover - defensive I/O guard
        messages.error(f"[{ecu_name}] writing YAML failed: {exc}")


def _modes_for_protocol(proto: str | None, pids_data: dict | None = None) -> tuple[int, ...]:
    """Pick the safe session-mode set for an ECU's ``id_protocol``.

    A KWP2000 ECU also gets the OEM sessions the profile's quirks permit on
    KWP2000 (e.g. Nissan's ``C0``), so the scan can confirm them.
    """
    if classify_protocol(proto) == KWP:
        extra = sorted(allowed_session_modes(pids_data, KWP) - set(KWP_SESSION_MODES))
        return KWP_SESSION_MODES + tuple(extra)
    return UDS_SESSION_MODES


async def scan_ecu_sessions(
    terminal: Terminal,
    ecu_name: str,
    tx_id: int,
    modes: tuple[int, ...],
    throttle_ms: int = 200,
    verbose: bool = False,
    write_yaml: bool = True,
    *,
    profile: Profile,
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

    paint = Painter(sys.stdout)
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
            print(
                f"    {paint(*POSITIVE)} 10 {mode:02X}: supported" + (f" ({name})" if name else "")
            )
        elif category == "wrong-session":
            # Rejected in the current session — record as unsupported-in-context.
            desc = response.get("nrc_desc", "")
            hit = SessionHit(mode=mode, name=name, supported=False, nrc=nrc, nrc_desc=desc)
            hits.append(hit)
            print(
                f"    {paint(*REFUSED)} 10 {mode:02X}: exists, rejected in the active session "
                f"(NRC 0x{nrc:02X} {desc})"
            )
        elif category == "not-supported":
            desc = response.get("nrc_desc", "")
            hit = SessionHit(mode=mode, name=name, supported=False, nrc=nrc, nrc_desc=desc)
            hits.append(hit)
            print(f"    {paint(*ABSENT)} 10 {mode:02X}: not supported (NRC 0x{nrc:02X} {desc})")
        else:
            if verbose:
                print(
                    f"    {paint(*PROBE_FAILED)} 10 {mode:02X}: {response.get('error', 'unknown')}"
                )

        if throttle_ms > 0:
            await asyncio.sleep(throttle_ms / 1000.0)

    if write_yaml and hits:
        _write_hit(ecu_name, hits, profile=profile)

    supported = sum(1 for h in hits if h.supported)
    print(f"  [{ecu_name}] done — {supported}/{len(hits)} session type(s) supported")
    return hits


async def mode_sessions_scan(
    terminal: Terminal,
    pids_data: dict,
    ecus: list[str],
    modes: tuple[int, ...] | None = None,
    throttle_ms: int = 200,
    verbose: bool = False,
    write_yaml: bool = True,
    *,
    profile: Profile,
) -> dict[str, list[SessionHit]]:
    """Probe the supported diagnostic session types on one or more ECUs.

    For each ECU the session-mode set is auto-selected from its ``id_protocol``
    (UDS → 01/03; KWP2000 → 81/82/83 plus any OEM session a profile quirk permits
    on KWP2000, e.g. Nissan's C0) unless an explicit ``modes`` tuple is given.
    Every mode is checked against :mod:`canair.safety.blocklist` for that ECU first; a
    refused one is skipped with its reason. Hits are written to each
    ``ecus/<ecu>.yaml`` under a ``sessions:`` section.

    Returns mapping of ECU name (upper-case) → list of :class:`SessionHit`.
    """
    ecu_defs: dict[str, dict] = {}
    for fname, fdata in pids_data.get("ecus", {}).items():
        if isinstance(fdata, dict):
            ecu_defs[fname.upper()] = fdata
    policy = SessionPolicy.from_profile(pids_data)

    results: dict[str, list[SessionHit]] = {}
    for ecu in ecus:
        key = ecu.upper()
        if key not in ecu_defs:
            messages.warning(f"ECU {ecu!r} not in the profile, skipping")
            continue
        tx_id = ecu_defs[key].get("tx_id")
        if tx_id is None:
            messages.warning(f"ECU {ecu!r} has no tx_id, skipping")
            continue

        if modes is not None:
            ecu_modes = modes
        else:
            proto = (ecu_defs[key].get("identity") or {}).get("id_protocol")
            ecu_modes = _modes_for_protocol(proto, pids_data)
        # Refused modes are skipped up front with the reason, rather than aborting
        # the whole scan when the terminal's guard refuses one mid-sweep.
        allowed_modes: list[int] = []
        for mode in ecu_modes:
            blocked = policy.check(mode, tx_id)
            if blocked:
                messages.warning(f"[{key}] skipping 10 {mode:02X}: {blocked}")
            else:
                allowed_modes.append(mode)

        results[key] = await scan_ecu_sessions(
            terminal,
            ecu_name=key,
            tx_id=tx_id,
            modes=tuple(allowed_modes),
            throttle_ms=throttle_ms,
            verbose=verbose,
            write_yaml=write_yaml,
            profile=profile,
        )

    print("\n  --- Diagnostic Session Type Scan Summary ---")
    for ecu_key, hit_list in results.items():
        supported = sum(1 for h in hit_list if h.supported)
        print(f"    {ecu_key}: {supported} supported, {len(hit_list) - supported} unsupported")

    return results
