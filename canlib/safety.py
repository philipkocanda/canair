"""Command safety policy and enforcement — shared across every transport.

The blocklist (:data:`BLOCKED_UDS_SERVICES` / :func:`check_command_safety`) is a
pure, transport-independent policy. :func:`enforce_command_safety` *applies* that
policy uniformly: every terminal routes outbound commands through it before they
reach the bus, so ``wican-ws`` and ``slcan-tcp`` (and any future transport) get
identical protection and identical ``--unsafe`` confirmation behavior. Never
re-implement the guard per transport.

Diagnostic session control (service ``0x10``) is protocol-dependent: the same
sub-function byte means different things to a UDS and a KWP2000 ECU (``10 82`` is
a KWP2000 diagnostic session but UDS *programmingSession* with the
suppress-positive-response bit). :func:`check_session_mode` is the one rule, used
at runtime (through a per-target :class:`SessionPolicy`) and by ``canair
validate``; an ECU whose protocol is unknown gets whichever reading is stricter.
RoutineControl (``0x31``) is judged the same way: its reprogramming routine ids
(:data:`BLOCKED_ROUTINES`) are refused on any ECU not known to speak KWP2000.

A *functional* (broadcast) request is judged by a separate, stricter rule,
:func:`check_functional_request`: an allow-list of reads that ``--unsafe`` cannot
widen, since one frame reaches every routed ECU at once.
"""

from __future__ import annotations

import asyncio
import sys
from collections.abc import Collection, Mapping
from dataclasses import dataclass, field, replace
from typing import Any, Final, Literal

from .log import log_command

# Services that write ECU memory, reflash firmware, reset an ECU or silence its
# traffic. Blocked by default to prevent accidental damage. The SIDs mean the same
# on UDS and KWP2000 except 0x38 (UDS file transfer, KWP2000 start-routine-at-address) and
# 0x39 and 0x3B, which exist only on KWP2000.
BLOCKED_UDS_SERVICES = {
    0x11: "ECUReset (restarts the ECU, unsafe while the car is in use)",
    0x28: "CommunicationControl (stops the ECU's normal CAN traffic)",
    0x2E: "WriteDataByIdentifier (write to ECU memory)",
    0x34: "RequestDownload (ECU reflash)",
    0x35: "RequestUpload (ECU memory dump)",
    0x36: "TransferData (flash data transfer)",
    0x37: "RequestTransferExit (finalize flash)",
    0x38: "RequestFileTransfer (UDS) / StartRoutineByAddress (KWP2000: runs code in ECU memory)",
    0x39: "StopRoutineByAddress (KWP2000)",
    0x3B: "WriteDataByLocalIdentifier (KWP2000 write to ECU memory)",
    0x3D: "WriteMemoryByAddress (write to ECU memory)",
    0x87: "LinkControl (changes the ECU's CAN bit rate)",
}

# RoutineControl (UDS 0x31) routines that erase, reflash, swap firmware banks or
# (de)activate paid features. ISO 14229-1 defines FF00/FF01; the rest are
# Hyundai/Kia's (ES95486-02), in the manufacturer range, so on another make the
# same id is some unrelated routine and the refusal is only a prompt (--unsafe).
BLOCKED_ROUTINES = {
    0xFF00: "eraseMemory",
    0xFF01: "checkProgrammingDependencies",
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
# startRoutine and stopRoutine act; requestRoutineResults (0x03) only reads.
_ROUTINE_ACTING_SUBS = frozenset({0x01, 0x02})


class SafetyBlocked(ValueError):
    """A command the safety policy refused (or the user declined under ``--unsafe``).

    A ``ValueError`` subclass so existing ``except ValueError`` callers keep
    working; the CLI and the live-session guard catch it by name to report
    ``error: …`` instead of a traceback.
    """


# Which protocol's DiagnosticSessionControl semantics apply to an ECU.
SessionProtocol = Literal["uds", "kwp", "unknown"]
UDS: Final[SessionProtocol] = "uds"
KWP: Final[SessionProtocol] = "kwp"
UNKNOWN: Final[SessionProtocol] = "unknown"


def session_protocol(id_protocol: object) -> SessionProtocol:
    """Classify a profile ``identity.id_protocol`` value for the session rule."""
    text = str(id_protocol or "").strip().upper()
    if text.startswith("KWP"):
        return KWP
    if text == "UDS":
        return UDS
    return UNKNOWN


@dataclass(frozen=True)
class SessionBlock:
    """Why a session sub-function is refused.

    ``hard`` blocks (programming and safety-system sessions) can never be widened
    by a profile allowance; the rest can (a quirk such as ``nissan_c0_session``).
    """

    reason: str
    hard: bool


# The UDS sub-function's low seven bits are the session type; bit 7 is
# suppressPosRspMsgIndicationBit (ISO 14229-1 DiagnosticSessionControl).
_UDS_SUPPRESS_BIT = 0x80
_UDS_PROGRAMMING = 0x02
_UDS_SAFETY_SYSTEM = 0x04
# ISO-reserved, but Hyundai/Kia's ES95486 implements it as "ECU Programming Mode",
# an alias of programmingSession (the UDS twin of KWP2000's 0x85).
_HKMC_PROGRAMMING_MODE = 0x05
# 0x40-0x5F vehicleManufacturerSpecific, 0x60-0x7E systemSupplierSpecific.
_UDS_OEM_BAND = range(0x40, 0x7F)
# KWP2000 StartDiagnosticSession modes that are plain diagnostic sessions:
# 0x81 standard, 0x82 (Hyundai/Kia periodic/EOL diagnostic), 0x83 extended.
_KWP_SAFE = frozenset({0x81, 0x82, 0x83})
_KWP_PROGRAMMING = 0x85


def _low_band_block(t: int, allowed: Collection[int]) -> SessionBlock | None:
    """The UDS rule for a session *type* (sub-function without the suppress bit)."""
    if t == _UDS_PROGRAMMING:
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
    if t == _UDS_SAFETY_SYSTEM:
        return SessionBlock(
            "DiagnosticSessionControl sub 0x04 (safetySystemDiagnosticSession) -- "
            "airbag/pyrotechnic deployment tests",
            hard=True,
        )
    if t in _UDS_OEM_BAND and t not in allowed:
        return SessionBlock(
            f"DiagnosticSessionControl session type 0x{t:02X} (manufacturer/supplier-specific "
            "UDS session) -- may be a programming/development mode",
            hard=False,
        )
    return None


def _uds_block(sub: int, allowed: Collection[int]) -> SessionBlock | None:
    t = sub & 0x7F
    if sub & _UDS_SUPPRESS_BIT and t == _UDS_PROGRAMMING:
        return SessionBlock(
            f"DiagnosticSessionControl sub 0x{sub:02X} (UDS programmingSession with the "
            "suppress-positive-response bit) -- required for flash/write operations",
            hard=True,
        )
    block = _low_band_block(t, allowed)
    if block is not None or not sub & _UDS_SUPPRESS_BIT:
        return block
    # The suppress-bit form of anything but default/extended (or an allowed OEM
    # session) serves no read-only purpose, and the same byte is a KWP2000 OEM
    # mode on an ECU whose id_protocol is wrong — so it stays refused.
    if t in (0x01, 0x03) or t in allowed:
        return None
    return SessionBlock(
        f"DiagnosticSessionControl sub 0x{sub:02X} (unrecognized session with the "
        "suppress-positive-response bit)",
        hard=False,
    )


def _kwp_block(sub: int, allowed: Collection[int]) -> SessionBlock | None:
    if sub < 0x80:
        return _low_band_block(sub, allowed)
    if sub == _KWP_PROGRAMMING:
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
    sub: int, protocol: SessionProtocol = UNKNOWN, allowed: Collection[int] = frozenset()
) -> SessionBlock | None:
    """Why ``10 {sub}`` is refused on an ECU speaking ``protocol``, or None if safe.

    ``allowed`` lists sub-functions the profile declared safe for *this* protocol
    (:func:`canlib.quirks.allowed_session_modes`); it never lifts a hard block. A
    UDS ECU is judged on ``sub & 0x7F``. KWP2000 programming (``0x85``) is refused
    on every protocol, in case ``id_protocol`` is wrong. An unknown protocol is
    refused when *either* reading refuses it.
    """
    if sub == _KWP_PROGRAMMING and protocol != KWP:
        return _kwp_block(sub, allowed)
    if protocol == UDS:
        return _uds_block(sub, allowed)
    if protocol == KWP:
        return _kwp_block(sub, allowed)
    return _uds_block(sub, allowed) or _kwp_block(sub, allowed)


def check_session_mode(
    sub: int, protocol: SessionProtocol = UNKNOWN, allowed: Collection[int] = frozenset()
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

    ``grants`` are exact ``(tx_id, request hex)`` session requests whose *soft*
    block is lifted. :meth:`from_profile` never sets them: only a guarded profile
    action (:mod:`canlib.actions`) adds them, through :meth:`granting`, for the
    duration of its own send.
    """

    protocols: Mapping[int, SessionProtocol] = field(default_factory=dict)
    allowed: Mapping[SessionProtocol, frozenset[int]] = field(default_factory=dict)
    grants: frozenset[tuple[int, str]] = frozenset()

    @classmethod
    def from_profile(cls, pids_data: Mapping[str, Any] | None) -> SessionPolicy:
        """Build the policy from loaded profile data (``ecus`` + ``quirks``)."""
        from .quirks import allowed_session_modes

        protocols: dict[int, SessionProtocol] = {}
        ecus = (pids_data or {}).get("ecus") or {}
        for ecu_def in ecus.values() if isinstance(ecus, Mapping) else ():
            if not isinstance(ecu_def, Mapping) or ecu_def.get("tx_id") is None:
                continue
            identity = ecu_def.get("identity")
            proto = session_protocol(
                identity.get("id_protocol") if isinstance(identity, Mapping) else None
            )
            tx = int(ecu_def["tx_id"])
            protocols[tx] = proto if protocols.get(tx, proto) == proto else UNKNOWN
        allowed = {p: allowed_session_modes(pids_data, p) for p in (UDS, KWP, UNKNOWN)}
        return cls(protocols=protocols, allowed=allowed)

    def protocol(self, tx_id: int | None) -> SessionProtocol:
        return UNKNOWN if tx_id is None else self.protocols.get(tx_id, UNKNOWN)

    def allowed_for(self, protocol: SessionProtocol) -> frozenset[int]:
        return self.allowed.get(protocol, frozenset())

    def check(self, sub: int, tx_id: int | None) -> str | None:
        """The session rule for ``10 {sub}`` sent to ``tx_id``."""
        protocol = self.protocol(tx_id)
        return check_session_mode(sub, protocol, self.allowed_for(protocol))

    def granting(self, grants: Collection[tuple[int, str]]) -> SessionPolicy:
        """This policy plus the exact session requests ``grants`` permits."""
        normalized = frozenset((tx, req.replace(" ", "").upper()) for tx, req in grants)
        return replace(self, grants=self.grants | normalized)

    def is_granted(self, request: str, tx_id: int | None) -> bool:
        """Whether ``request`` to ``tx_id`` is one of this policy's exact grants."""
        return tx_id is not None and (tx_id, request) in self.grants

    def safe_modes(self, tx_id: int | None, candidates: Collection[str]) -> list[str]:
        """The 2-hex-digit ``candidates`` this policy permits on ``tx_id``."""
        return [m for m in candidates if self.check(int(m, 16), tx_id) is None]


_NO_POLICY: Final = SessionPolicy()


def check_command_safety(
    cmd: str, policy: SessionPolicy | None = None, tx_id: int | None = None
) -> str | None:
    """Check if a command is potentially dangerous.

    Returns an error message if the command is blocked, or None if safe.
    Checks both raw UDS hex commands and AT commands.

    ``policy``/``tx_id`` give a session request (``10 xx``) its target's protocol
    and the profile's allowances; without them the stricter reading applies.
    """
    clean = cmd.strip().upper()

    if clean.startswith("AT"):
        return None

    hex_only = clean.replace(" ", "")
    if not hex_only or not all(c in "0123456789ABCDEF" for c in hex_only):
        return None

    if len(hex_only) < 2:
        return None

    service_byte = int(hex_only[:2], 16)

    if service_byte in BLOCKED_UDS_SERVICES:
        desc = BLOCKED_UDS_SERVICES[service_byte]
        return f"BLOCKED: UDS service 0x{service_byte:02X} -- {desc}"

    if service_byte == 0x10 and len(hex_only) >= 4:
        active = policy or _NO_POLICY
        sub = int(hex_only[2:4], 16)
        protocol = active.protocol(tx_id)
        block = session_mode_block(sub, protocol, active.allowed_for(protocol))
        if block is not None and not block.hard and active.is_granted(hex_only, tx_id):
            return None
        return active.check(sub, tx_id)

    if service_byte == 0x31 and len(hex_only) >= 8:
        # On KWP2000, 0x31 is StartRoutineByLocalIdentifier: byte 2 is a local id.
        if (policy or _NO_POLICY).protocol(tx_id) != KWP:
            return check_routine(int(hex_only[2:4], 16), int(hex_only[4:8], 16))

    return None


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
    ("3E00", "TesterPresent"),
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
    clean = request.replace(" ", "").upper()
    if len(clean) % 2 or not clean or not all(c in "0123456789ABCDEF" for c in clean):
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
    type ``YES`` to proceed; anything else declines and raises.
    """
    blocked = check_command_safety(cmd, policy, tx_id)
    if not blocked:
        return

    if not unsafe:
        log_command(f"{cmd}  !! {blocked}")
        raise SafetyBlocked(blocked)

    print(f"\n  !! WARNING: {blocked}", file=sys.stderr)
    print("  !! --unsafe mode is active. The user MUST be consulted and", file=sys.stderr)
    print("  !! must explicitly give consent before this command is executed.", file=sys.stderr)
    print("  !! This command can cause irreversible damage to vehicle ECUs.", file=sys.stderr)
    try:
        confirm = await asyncio.get_event_loop().run_in_executor(
            None,
            lambda: input("  !! Type 'YES' to execute, anything else to skip: "),
        )
    except (EOFError, KeyboardInterrupt):
        confirm = ""
    if confirm.strip() != "YES":
        log_command(f"{cmd}  !! {blocked} -- user declined")
        raise SafetyBlocked(f"Command declined by user: {cmd}")
    log_command(f"{cmd}  !! {blocked} -- user confirmed (unsafe mode)")
