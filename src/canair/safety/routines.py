"""RoutineControl policy: the routine ids canair refuses to start or stop.

UDS ``0x31`` (RoutineControl) is judged by the routine id; on an ECU the profile says speaks
KWP2000, ``0x31`` is StartRoutineByLocalIdentifier and is judged by its one-byte local id.
:mod:`canair.safety.blocklist` decides which reading applies and calls these checks.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Final

from canair.uds.subfunctions import RoutineControlType

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
# KWP2000 StartRoutineByLocalIdentifier (0x31) routines judged where the profile says the
# ECU speaks KWP2000; there byte 1 is a one-byte local id. On the Hyundai/Kia OBC and VCU
# (firmware read in profiles/hyundai-ioniq-ae-ev/research/OBC_FIRMWARE_FINDINGS.md and
# VCU_FIRMWARE_FINDINGS.md) 00 and 01 leave the application for the bootloader. On the VCU F1
# (with the argument EE FF 00 11) switches the watchdog off and hands the ECU to a bench loader
# that waits on a bus the OBD port does not reach, so it stays hung until power is removed, and
# F3 (CC DD 33 44) starts a board self-test loop that runs until a reset. Both need only session
# 90, which asks for no key. On another make the same id is an unrelated routine, so the refusal
# is only a prompt (--unsafe).
BLOCKED_KWP_ROUTINES: Final[Mapping[int, str]] = {
    0x00: "enters the bootloader on the Hyundai/Kia OBC and VCU",
    0x01: "enters the bootloader on the Hyundai/Kia OBC and VCU",
    0xF1: "hangs the Hyundai/Kia VCU in a bench loader, watchdog off, even while the car is READY",
    0xF3: "starts a board self-test loop on the Hyundai/Kia VCU that runs until it is reset",
}
# ISO 14229-1 Annex F: E200 executes the scrapping program loader, E201 is
# DeployLoopRoutineID and E202-E2FF are safetySystemRoutineIDs (ISO 26021). They deploy
# or disable airbags and seatbelt pretensioners, which can injure a person, so no flag
# lifts the refusal.
SAFETY_SYSTEM_ROUTINES: Final = range(0xE200, 0xE300)
# startRoutine and stopRoutine act; requestRoutineResults (0x03) only reads.
ROUTINE_ACTING_SUBS = frozenset({RoutineControlType.START_ROUTINE, RoutineControlType.STOP_ROUTINE})


def check_safety_system_routine(sub: int, rid: int) -> str | None:
    """Why UDS ``31 {sub} {rid}`` starts or stops a safety-system routine, or None."""
    if rid not in SAFETY_SYSTEM_ROUTINES or (sub & 0x7F) not in ROUTINE_ACTING_SUBS:
        return None
    return (
        f"BLOCKED: RoutineControl 0x{rid:04X} is a safety-system routine (ISO 26021: deploys "
        "or disables airbags and seatbelt pretensioners); canair never sends it, with or "
        "without --unsafe"
    )


def check_routine(sub: int, rid: int) -> str | None:
    """Why UDS ``31 {sub} {rid}`` is refused, or None if it may be sent."""
    name = BLOCKED_ROUTINES.get(rid)
    if name is None or (sub & 0x7F) not in ROUTINE_ACTING_SUBS:
        return None
    return (
        f"BLOCKED: RoutineControl 0x{rid:04X} ({name}) -- reprogramming routine; "
        "re-run with --unsafe if you are certain it is safe"
    )


def check_kwp_routine(local_id: int) -> str | None:
    """Why KWP2000 ``31 {local_id}`` (StartRoutineByLocalIdentifier) is refused, or None."""
    effect = BLOCKED_KWP_ROUTINES.get(local_id)
    if effect is None:
        return None
    return (
        f"BLOCKED: KWP2000 StartRoutineByLocalIdentifier 0x{local_id:02X} -- {effect}; "
        "re-run with --unsafe if you are certain it is safe"
    )
