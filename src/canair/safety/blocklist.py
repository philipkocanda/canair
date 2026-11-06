"""Command safety policy and enforcement — shared across every transport.

The blocklist (:data:`BLOCKED_UDS_SERVICES` / :func:`check_command_safety`) is a
pure, transport-independent policy. :func:`enforce_command_safety` *applies* that
policy uniformly: every terminal routes outbound commands through it before they
reach the bus, so ``wican-ws`` and ``slcan-tcp`` (and any future transport) get
identical protection and identical ``--unsafe`` confirmation behavior. Never
re-implement the guard per transport.

A few services are refused only for the requests that change state: ControlDTCSetting
off, AccessTimingParameter writes, and starting periodic or event-driven replies
(:data:`GUARDED_SERVICES`). The request that reads, or that undoes the service's effect,
passes.

Diagnostic session control (service ``0x10``) is protocol-dependent: the same
sub-function byte means different things to a UDS and a KWP2000 ECU (``10 82`` is
a KWP2000 diagnostic session but UDS *programmingSession* with the
suppress-positive-response bit). :func:`check_session_mode` is the one rule, used
at runtime (through a per-target :class:`SessionPolicy`) and by ``canair
validate``; an ECU whose protocol is unknown gets whichever reading is stricter.
RoutineControl (``0x31``) is judged the same way: its reprogramming routine ids
(:data:`BLOCKED_ROUTINES`) are refused on any ECU not known to speak KWP2000. The
ISO 14229-1 safety-system routine ids (:data:`SAFETY_SYSTEM_ROUTINES`, which deploy
airbags and pretensioners) are refused there too, and ``--unsafe`` cannot lift that
(:func:`safety_system_block`).

A *functional* (broadcast) request is judged by a separate, stricter rule,
:func:`check_functional_request`: an allow-list of reads that ``--unsafe`` cannot
widen, since one frame reaches every routed ECU at once. It applies to every request
sent to a functional id that no ECU of the profile is registered on, whichever command
sends it (``discover --functional``, ``raw``, the REPL), and only an exact grant of a
guarded profile action lifts it.

Anything that is neither hex bytes nor an AT command (an STN ``STPX``, which sends
arbitrary frames) is refused: what the guard cannot read, it does not let through.
"""

from __future__ import annotations

import asyncio
import sys
from collections.abc import Collection, Mapping
from dataclasses import dataclass, field, replace
from typing import Any, Final

from canair.core.prompt import typed_confirm
from canair.safety.quirks import allowed_session_modes
from canair.uds.addressing import FUNCTIONAL_11BIT_ID, FUNCTIONAL_29BIT_ID
from canair.uds.protocol import KWP, UDS, UNKNOWN, DiagProtocol, classify_protocol
from canair.uds.services import (
    ACCESS_TIMING_PARAMETER,
    CONTROL_DTC_SETTING,
    DIAGNOSTIC_SESSION_CONTROL,
    READ_DATA_BY_PERIODIC_IDENTIFIER,
    RESPONSE_ON_EVENT,
    ROUTINE_CONTROL,
    TESTER_PRESENT_REQUEST,
    service_name,
)
from canair.uds.subfunctions import (
    OEM_SESSION_TYPES,
    SUPPRESS_POSITIVE_RESPONSE,
    DtcSettingType,
    KwpSessionType,
    PeriodicTransmissionMode,
    ResponseOnEventType,
    RoutineControlType,
    SessionType,
    TimingParameterAccessType,
)

# Services that write ECU memory, reflash firmware, reset an ECU, silence its
# traffic or carry a request the guard cannot read. Blocked by default to prevent
# accidental damage. The SIDs mean the same on UDS and KWP2000 except 0x2C (UDS
# DynamicallyDefineDataIdentifier, KWP2000 DynamicallyDefineLocalIdentifier: both define
# data in the ECU), 0x38 (UDS file transfer, KWP2000 start-routine-at-address) and 0x39
# and 0x3B, which exist only on KWP2000.
BLOCKED_UDS_SERVICES = {
    0x11: "ECUReset (restarts the ECU, unsafe while the car is in use)",
    0x28: "CommunicationControl (stops the ECU's normal CAN traffic)",
    0x29: "Authentication (a certificate exchange that changes the tester's access rights)",
    0x2C: (
        "DynamicallyDefineDataIdentifier (defines or clears data identifiers in the ECU; "
        "by memory address it reads memory past the ReadMemoryByAddress rules)"
    ),
    0x2E: "WriteDataByIdentifier (write to ECU memory)",
    0x34: "RequestDownload (ECU reflash)",
    0x35: "RequestUpload (ECU memory dump)",
    0x36: "TransferData (flash data transfer)",
    0x37: "RequestTransferExit (finalize flash)",
    0x38: "RequestFileTransfer (UDS) / StartRoutineByAddress (KWP2000: runs code in ECU memory)",
    0x39: "StopRoutineByAddress (KWP2000)",
    0x3B: "WriteDataByLocalIdentifier (KWP2000 write to ECU memory)",
    0x3D: "WriteMemoryByAddress (write to ECU memory)",
    0x84: "SecuredDataTransmission (carries another request, which the blocklist cannot judge)",
    0x87: "LinkControl (changes the ECU's CAN bit rate)",
}


@dataclass(frozen=True)
class GuardedService:
    """A service refused unless the byte after its SID is one that only reads or undoes it.

    ``mask`` selects the bits of that byte that name the request: bit 7 of a sub-function
    is the suppress-positive-response bit, which does not change what the request does.
    """

    effect: str
    allowed: frozenset[int]
    mask: int = 0x7F


GUARDED_SERVICES: Final[Mapping[int, GuardedService]] = {
    READ_DATA_BY_PERIODIC_IDENTIFIER: GuardedService(
        "the ECU sends replies on its own until told to stop, which can desync the link",
        frozenset({PeriodicTransmissionMode.STOP_SENDING}),
        mask=0xFF,  # a transmissionMode, not a sub-function: it has no suppress bit
    ),
    ACCESS_TIMING_PARAMETER: GuardedService(
        "changes the ECU's timing parameters",
        frozenset(
            {
                TimingParameterAccessType.READ_EXTENDED_TIMING_PARAMETER_SET,
                TimingParameterAccessType.READ_CURRENTLY_ACTIVE_TIMING_PARAMETERS,
            }
        ),
    ),
    CONTROL_DTC_SETTING: GuardedService(
        "the ECU stops recording DTCs until DTC setting is on again or the session ends",
        frozenset({DtcSettingType.ON}),
    ),
    # Bit 6 is storageState, so the mask keeps it: a stored event is another request.
    RESPONSE_ON_EVENT: GuardedService(
        "the ECU sends unrequested replies whenever an event fires, which can desync the link",
        frozenset(
            {
                ResponseOnEventType.STOP_RESPONSE_ON_EVENT,
                ResponseOnEventType.REPORT_ACTIVATED_EVENTS,
            }
        ),
    ),
}

# RoutineControl (UDS 0x31) routines that erase, reflash, swap firmware banks or
# (de)activate paid features. ISO 14229-1 defines FF00/FF01, and its 2006 edition FF02;
# the rest are Hyundai/Kia's (ES95486-02), in the manufacturer range, so on another make
# the same id is some unrelated routine and the refusal is only a prompt (--unsafe).
BLOCKED_ROUTINES = {
    0xFF00: "eraseMemory",
    0xFF01: "checkProgrammingDependencies",
    0xFF02: "eraseMirrorMemoryDTCs (ISO 14229-1:2006)",
    0x0211: "FinishUpdate / CopyActiveAreaTo (Hyundai/Kia)",
    0x0212: "EraseTargetArea (Hyundai/Kia)",
    0x0213: "SwapActiveArea (Hyundai/Kia)",
    0x0220: "WriteDeltaPatch (Hyundai/Kia)",
    0x0301: "EnableUpdateCondition (Hyundai/Kia OTA)",
    0x0410: "PrepareFoDActivation (Hyundai/Kia features on demand)",
    0x0411: "ExecuteFoDActivation (Hyundai/Kia features on demand)",
    0x0412: "ConfirmFoDActivation (Hyundai/Kia features on demand)",
    0x0420: "PrepareFoDDeactivation (Hyundai/Kia features on demand)",
    0x0421: "ExecuteFoDDeactivation (Hyundai/Kia features on demand)",
    0x0422: "ConfirmFoDDeactivation (Hyundai/Kia features on demand)",
    0x0440: "DeleteFoDCertificates (Hyundai/Kia features on demand)",
}
# ISO 14229-1 Annex F: E200 executes the scrapping program loader, E201 is
# DeployLoopRoutineID and E202-E2FF are safetySystemRoutineIDs (ISO 26021). They deploy
# or disable airbags and seatbelt pretensioners, which can injure a person, so no flag
# lifts the refusal.
SAFETY_SYSTEM_ROUTINES: Final = range(0xE200, 0xE300)
# startRoutine and stopRoutine act; requestRoutineResults (0x03) only reads.
_ROUTINE_ACTING_SUBS = frozenset(
    {RoutineControlType.START_ROUTINE, RoutineControlType.STOP_ROUTINE}
)


class SafetyBlocked(ValueError):
    """A command the safety policy refused (or the user declined under ``--unsafe``).

    A ``ValueError`` subclass so existing ``except ValueError`` callers keep
    working; the CLI and the live-session guard catch it by name to report
    ``error: …`` instead of a traceback.
    """


class CommandDeclined(SafetyBlocked):
    """The user answered no to the ``--unsafe`` consent prompt (the run ends, exit 1)."""


@dataclass(frozen=True)
class SessionBlock:
    """Why a session sub-function is refused.

    ``hard`` blocks (programming and safety-system sessions) can never be widened
    by a profile allowance; the rest can (a quirk such as ``nissan_c0_session``).
    """

    reason: str
    hard: bool


# The UDS sub-function's low seven bits are the session type; bit 7 is
# SUPPRESS_POSITIVE_RESPONSE (ISO 14229-1 DiagnosticSessionControl). The default and
# extended sessions only read.
_UDS_READ_SESSIONS = frozenset(
    {SessionType.DEFAULT_SESSION, SessionType.EXTENDED_DIAGNOSTIC_SESSION}
)
# ISO-reserved, but Hyundai/Kia's ES95486 implements it as "ECU Programming Mode",
# an alias of programmingSession (the UDS twin of KWP2000's 0x85).
_HKMC_PROGRAMMING_MODE = 0x05
# KWP2000 StartDiagnosticSession modes that are plain diagnostic sessions:
# 0x81 standard, 0x82 (Hyundai/Kia periodic/EOL diagnostic), 0x83 extended.
_KWP_SAFE = frozenset(
    {
        KwpSessionType.STANDARD_DIAGNOSTIC_SESSION,
        KwpSessionType.PERIODIC_DIAGNOSTIC_SESSION,
        KwpSessionType.EXTENDED_DIAGNOSTIC_SESSION,
    }
)


def _low_band_block(t: int, allowed: Collection[int]) -> SessionBlock | None:
    """The UDS rule for a session *type* (sub-function without the suppress bit)."""
    if t == SessionType.PROGRAMMING_SESSION:
        return SessionBlock(
            "DiagnosticSessionControl sub 0x02 (programmingSession) -- required for "
            "flash/write operations",
            hard=True,
        )
    if t == _HKMC_PROGRAMMING_MODE:
        return SessionBlock(
            "DiagnosticSessionControl sub 0x05 (ECU programming mode on Hyundai/Kia, an alias "
            "of programmingSession) -- required for flash/write operations",
            hard=True,
        )
    if t == SessionType.SAFETY_SYSTEM_DIAGNOSTIC_SESSION:
        return SessionBlock(
            "DiagnosticSessionControl sub 0x04 (safetySystemDiagnosticSession) -- "
            "airbag/pyrotechnic deployment tests",
            hard=True,
        )
    if t in OEM_SESSION_TYPES and t not in allowed:
        return SessionBlock(
            f"DiagnosticSessionControl session type 0x{t:02X} (manufacturer/supplier-specific "
            "UDS session) -- may be a programming/development mode",
            hard=False,
        )
    return None


def _uds_block(sub: int, allowed: Collection[int]) -> SessionBlock | None:
    t = sub & 0x7F
    if sub & SUPPRESS_POSITIVE_RESPONSE and t == SessionType.PROGRAMMING_SESSION:
        return SessionBlock(
            f"DiagnosticSessionControl sub 0x{sub:02X} (UDS programmingSession with the "
            "suppress-positive-response bit) -- required for flash/write operations",
            hard=True,
        )
    block = _low_band_block(t, allowed)
    if block is not None or not sub & SUPPRESS_POSITIVE_RESPONSE:
        return block
    # The suppress-bit form of anything but default/extended (or an allowed OEM
    # session) serves no read-only purpose, and the same byte is a KWP2000 OEM
    # mode on an ECU whose id_protocol is wrong — so it stays refused.
    if t in _UDS_READ_SESSIONS or t in allowed:
        return None
    return SessionBlock(
        f"DiagnosticSessionControl sub 0x{sub:02X} (unrecognized session with the "
        "suppress-positive-response bit)",
        hard=False,
    )


def _kwp_block(sub: int, allowed: Collection[int]) -> SessionBlock | None:
    if sub < 0x80:
        return _low_band_block(sub, allowed)
    if sub == KwpSessionType.PROGRAMMING_SESSION:
        return SessionBlock(
            "StartDiagnosticSession sub 0x85 (KWP2000 ECU programming mode) -- required "
            "for flash/write operations",
            hard=True,
        )
    if sub in _KWP_SAFE or sub in allowed:
        return None
    return SessionBlock(
        f"StartDiagnosticSession sub 0x{sub:02X} (unrecognized KWP2000 session mode) -- "
        "may be a programming/development mode",
        hard=False,
    )


def session_mode_block(
    sub: int, protocol: DiagProtocol = UNKNOWN, allowed: Collection[int] = frozenset()
) -> SessionBlock | None:
    """Why ``10 {sub}`` is refused on an ECU speaking ``protocol``, or None if safe.

    ``allowed`` lists sub-functions the profile declared safe for *this* protocol
    (:func:`canair.safety.quirks.allowed_session_modes`); it never lifts a hard block. A
    UDS ECU is judged on ``sub & 0x7F``. KWP2000 programming (``0x85``) is refused
    on every protocol, in case ``id_protocol`` is wrong. An unknown protocol is
    refused when *either* reading refuses it.
    """
    if sub == KwpSessionType.PROGRAMMING_SESSION and protocol != KWP:
        return _kwp_block(sub, allowed)
    if protocol == UDS:
        return _uds_block(sub, allowed)
    if protocol == KWP:
        return _kwp_block(sub, allowed)
    return _uds_block(sub, allowed) or _kwp_block(sub, allowed)


def check_session_mode(
    sub: int, protocol: DiagProtocol = UNKNOWN, allowed: Collection[int] = frozenset()
) -> str | None:
    """:func:`session_mode_block` as the ``BLOCKED: …`` message the guard reports."""
    block = session_mode_block(sub, protocol, allowed)
    if block is None:
        return None
    hint = "" if block.hard else "; re-run with --unsafe if you are certain it is safe"
    return f"BLOCKED: {block.reason}{hint}"


@dataclass(frozen=True)
class SessionPolicy:
    """Per-target session context: each ECU's protocol plus the profile's allowances.

    Keyed by request (TX) id. A TX id shared by ECUs of different protocols, or
    not registered at all (a discovery sweep), resolves to ``unknown``. The
    default instance knows nothing, so the stricter reading always applies.

    ``grants`` are exact ``(tx_id, request hex)`` requests whose *soft* session block
    is lifted, and which may go to a functional id although they are not one of
    :data:`FUNCTIONAL_READS`. :meth:`from_profile` never sets them: only a guarded
    profile action (:mod:`canair.actions`) adds them, through :meth:`granting`, for
    the duration of its own send.
    """

    protocols: Mapping[int, DiagProtocol] = field(default_factory=dict)
    allowed: Mapping[DiagProtocol, frozenset[int]] = field(default_factory=dict)
    grants: frozenset[tuple[int, str]] = frozenset()

    @classmethod
    def from_profile(cls, pids_data: Mapping[str, Any] | None) -> SessionPolicy:
        """Build the policy from loaded profile data (``ecus`` + ``quirks``)."""
        protocols: dict[int, DiagProtocol] = {}
        ecus = (pids_data or {}).get("ecus") or {}
        for ecu_def in ecus.values() if isinstance(ecus, Mapping) else ():
            if not isinstance(ecu_def, Mapping) or ecu_def.get("tx_id") is None:
                continue
            identity = ecu_def.get("identity")
            proto = classify_protocol(
                identity.get("id_protocol") if isinstance(identity, Mapping) else None
            )
            tx = int(ecu_def["tx_id"])
            protocols[tx] = proto if protocols.get(tx, proto) == proto else UNKNOWN
        allowed = {p: allowed_session_modes(pids_data, p) for p in (UDS, KWP, UNKNOWN)}
        return cls(protocols=protocols, allowed=allowed)

    def protocol(self, tx_id: int | None) -> DiagProtocol:
        return UNKNOWN if tx_id is None else self.protocols.get(tx_id, UNKNOWN)

    def registered(self, tx_id: int) -> bool:
        """Whether an ECU of the profile is addressed on ``tx_id``."""
        return tx_id in self.protocols

    def allowed_for(self, protocol: DiagProtocol) -> frozenset[int]:
        return self.allowed.get(protocol, frozenset())

    def check(self, sub: int, tx_id: int | None) -> str | None:
        """The session rule for ``10 {sub}`` sent to ``tx_id``."""
        protocol = self.protocol(tx_id)
        return check_session_mode(sub, protocol, self.allowed_for(protocol))

    def granting(self, grants: Collection[tuple[int, str]]) -> SessionPolicy:
        """This policy plus the exact session requests ``grants`` permits."""
        normalized = frozenset((tx, _compact_hex(req)) for tx, req in grants)
        return replace(self, grants=self.grants | normalized)

    def is_granted(self, request: str, tx_id: int | None) -> bool:
        """Whether ``request`` to ``tx_id`` is one of this policy's exact grants."""
        return tx_id is not None and (tx_id, request) in self.grants

    def safe_modes(self, tx_id: int | None, candidates: Collection[str]) -> list[str]:
        """The 2-hex-digit ``candidates`` this policy permits on ``tx_id``."""
        return [m for m in candidates if self.check(int(m, 16), tx_id) is None]


_NO_POLICY: Final = SessionPolicy()


def _compact_hex(text: str) -> str:
    """``text`` upper-cased with every whitespace character removed.

    ``bytes.fromhex`` skips any whitespace, tabs and newlines included, so the guard
    must judge the same bytes the transport will send.
    """
    return "".join(text.split()).upper()


def _is_hex(text: str) -> bool:
    return bool(text) and all(c in "0123456789ABCDEF" for c in text)


def functional_block(
    cmd: str, policy: SessionPolicy | None = None, tx_id: int | None = None
) -> str | None:
    """Why ``cmd`` may not be sent to ``tx_id`` as a broadcast, or None.

    Applies to a functional id no ECU of the profile is registered on (a profile may
    declare a functional-TX ECU, which is then addressed like any other). An odd
    length is an ELM327 request plus its expected-frame-count digit, which never
    reaches the bus, so it is judged without it. ``--unsafe`` cannot lift this
    refusal; an exact grant (:meth:`SessionPolicy.granting`) can.
    """

    if tx_id not in (FUNCTIONAL_11BIT_ID, FUNCTIONAL_29BIT_ID):
        return None
    active = policy or _NO_POLICY
    request = _compact_hex(cmd)
    if request.startswith("AT") or active.registered(tx_id):
        return None
    if active.is_granted(request, tx_id):
        return None
    if len(request) % 2 and _is_hex(request):
        request = request[:-1]
    return check_functional_request(request)


def check_command_safety(
    cmd: str, policy: SessionPolicy | None = None, tx_id: int | None = None
) -> str | None:
    """Check if a command is potentially dangerous.

    Returns an error message if the command is blocked, or None if safe.
    Checks both raw UDS hex commands and AT commands; anything else is refused.

    ``policy``/``tx_id`` give a session request (``10 xx``) its target's protocol
    and the profile's allowances; without them the stricter reading applies. A
    request to an unregistered functional id must also be one of
    :data:`FUNCTIONAL_READS` (:func:`functional_block`).
    """
    hex_only = _compact_hex(cmd)

    if hex_only.startswith("AT") or not hex_only:
        return None

    if not _is_hex(hex_only):
        return (
            f"BLOCKED: {cmd.strip()!r} is neither hex bytes nor an AT command, so the "
            "blocklist cannot judge what it sends; re-run with --unsafe if you are certain "
            "it is safe"
        )

    broadcast = functional_block(hex_only, policy, tx_id)
    if broadcast is not None:
        return broadcast

    if len(hex_only) < 2:
        return None

    service_byte = int(hex_only[:2], 16)

    if service_byte in BLOCKED_UDS_SERVICES:
        desc = BLOCKED_UDS_SERVICES[service_byte]
        return f"BLOCKED: UDS service 0x{service_byte:02X} -- {desc}"

    if service_byte in GUARDED_SERVICES:
        sub_text = hex_only[2:4]
        return check_guarded_service(
            service_byte, int(sub_text, 16) if len(sub_text) == 2 else None
        )

    if service_byte == DIAGNOSTIC_SESSION_CONTROL and len(hex_only) >= 4:
        active = policy or _NO_POLICY
        sub = int(hex_only[2:4], 16)
        protocol = active.protocol(tx_id)
        block = session_mode_block(sub, protocol, active.allowed_for(protocol))
        if block is not None and not block.hard and active.is_granted(hex_only, tx_id):
            return None
        return active.check(sub, tx_id)

    if service_byte == ROUTINE_CONTROL and len(hex_only) >= 8:
        # On KWP2000, 0x31 is StartRoutineByLocalIdentifier: byte 2 is a local id.
        if (policy or _NO_POLICY).protocol(tx_id) != KWP:
            sub, rid = int(hex_only[2:4], 16), int(hex_only[4:8], 16)
            return check_safety_system_routine(sub, rid) or check_routine(sub, rid)

    return None


def check_guarded_service(sid: int, sub: int | None) -> str | None:
    """Why ``{sid} {sub}`` is refused for a service of :data:`GUARDED_SERVICES`, or None.

    ``sub`` is the byte after the SID, None when the request stops at the SID: the
    guard cannot tell what such a request does, so it is refused.
    """
    guard = GUARDED_SERVICES[sid]
    if sub is not None and (sub & guard.mask) in guard.allowed:
        return None
    name = service_name(sid) or f"service 0x{sid:02X}"
    which = f"0x{sub:02X}" if sub is not None else "with no sub-function"
    return (
        f"BLOCKED: {name} {which} -- {guard.effect}; re-run with --unsafe if you are "
        "certain it is safe"
    )


def check_safety_system_routine(sub: int, rid: int) -> str | None:
    """Why UDS ``31 {sub} {rid}`` starts or stops a safety-system routine, or None."""
    if rid not in SAFETY_SYSTEM_ROUTINES or (sub & 0x7F) not in _ROUTINE_ACTING_SUBS:
        return None
    return (
        f"BLOCKED: RoutineControl 0x{rid:04X} is a safety-system routine (ISO 26021: deploys "
        "or disables airbags and seatbelt pretensioners); canair never sends it, with or "
        "without --unsafe"
    )


def safety_system_block(
    cmd: str, policy: SessionPolicy | None = None, tx_id: int | None = None
) -> str | None:
    """Why ``cmd`` to ``tx_id`` starts or stops a safety-system routine, or None.

    ``--unsafe`` cannot lift this refusal. A KWP2000 ECU is not judged: there ``0x31``
    is StartRoutineByLocalIdentifier, whose byte 2 is a local id.
    """
    request = _compact_hex(cmd)
    if len(request) < 8 or not _is_hex(request) or int(request[:2], 16) != ROUTINE_CONTROL:
        return None
    if (policy or _NO_POLICY).protocol(tx_id) == KWP:
        return None
    return check_safety_system_routine(int(request[2:4], 16), int(request[4:8], 16))


def unliftable_block(
    cmd: str, policy: SessionPolicy | None = None, tx_id: int | None = None
) -> str | None:
    """Why ``cmd`` to ``tx_id`` is refused with or without ``--unsafe``, or None."""
    return functional_block(cmd, policy, tx_id) or safety_system_block(cmd, policy, tx_id)


def check_routine(sub: int, rid: int) -> str | None:
    """Why UDS ``31 {sub} {rid}`` is refused, or None if it may be sent."""
    name = BLOCKED_ROUTINES.get(rid)
    if name is None or (sub & 0x7F) not in _ROUTINE_ACTING_SUBS:
        return None
    return (
        f"BLOCKED: RoutineControl 0x{rid:04X} ({name}) -- reprogramming routine; "
        "re-run with --unsafe if you are certain it is safe"
    )


# The only requests a functional sweep may broadcast, as (shape, what it reads).
# A functional frame reaches every ECU the gateway routes it to at once, so the
# rule is an allow-list rather than the blocklist, and --unsafe cannot widen it.
FUNCTIONAL_READS: Final = (
    (TESTER_PRESENT_REQUEST, "TesterPresent"),
    ("22xxxx", "ReadDataByIdentifier, one DID"),
    ("1Axx", "KWP2000 ReadEcuIdentification"),
    ("1902xx", "ReadDTCInformation, reportDTCByStatusMask"),
    ("1800FF00", "KWP2000 ReadDiagnosticTroubleCodesByStatus, all groups"),
    ("01xx", "OBD-II mode 01 current data, one PID"),
)


def _matches_shape(request: str, shape: str) -> bool:
    return len(request) == len(shape) and all(
        s == "x" or s == r for s, r in zip(shape, request, strict=True)
    )


def check_functional_request(request: str) -> str | None:
    """Why ``request`` may not be sent functionally, or None if it is an allowed read.

    Session control, resets, DTC clears, communication control, DTC setting,
    writes, IOControl and routines all fall outside :data:`FUNCTIONAL_READS`, so
    they are refused; ``--unsafe`` plays no part.
    """
    clean = _compact_hex(request)
    if len(clean) % 2 or not _is_hex(clean):
        return f"BLOCKED: functional request {request!r} is not hex bytes"
    if any(_matches_shape(clean, shape) for shape, _ in FUNCTIONAL_READS):
        return None
    allowed = ", ".join(shape for shape, _ in FUNCTIONAL_READS)
    return (
        f"BLOCKED: {clean} may not be sent functionally: a broadcast reaches every ECU at once, "
        f"so only these reads are allowed ({allowed}), with or without --unsafe"
    )


async def enforce_command_safety(
    cmd: str, unsafe: bool, policy: SessionPolicy | None = None, tx_id: int | None = None
) -> None:
    """Enforce the command blocklist before a command is sent.

    Raises :class:`SafetyBlocked` if ``cmd`` is blocked and not permitted. In
    default (safe) mode a blocked command is refused outright. With
    ``unsafe=True`` the user is prompted for interactive confirmation and must
    type ``YES`` to proceed; anything else declines and raises. A broadcast outside
    the allow-list and a safety-system routine are refused even then.
    """
    blocked = check_command_safety(cmd, policy, tx_id)
    if not blocked:
        return

    if not unsafe or unliftable_block(cmd, policy, tx_id) is not None:
        raise SafetyBlocked(blocked)

    if not sys.stdin.isatty():
        raise SafetyBlocked(f"{blocked} (--unsafe needs a terminal to ask for consent)")
    question = (
        f"{blocked}\n!! --unsafe mode is active, so you must consent before this is sent."
        f"\n!! It can cause irreversible damage to vehicle ECUs. Send {cmd}?"
    )
    consented = await asyncio.get_event_loop().run_in_executor(
        None, lambda: typed_confirm(question, "YES")
    )
    if not consented:
        raise CommandDeclined(f"Command declined by user: {cmd}")
