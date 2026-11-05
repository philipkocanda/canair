"""Builders for the requests canair sends, as the hex text every terminal takes.

A request's layout (which byte comes where) is a protocol fact like the bytes themselves, so
code that sends a session change, an I/O control, a routine control or a DTC clear builds it
here from named values instead of spelling ``f"2F{did}03…"`` by hand. What a field *means*
(``IoControlParameter.SHORT_TERM_ADJUSTMENT`` drives the output) then shows at the call.

Read requests (``22 DID``, ``21 LID``) stay hex templates where they are built: their layout
is the standard's notation, and echo validation catches a wrong one. See
plans/2026-11-05-named-protocol-constants.md.

Every builder checks that each field fits its width and raises ``ValueError`` if not.
"""

from __future__ import annotations

from canair.uds.services import (
    CLEAR_DIAGNOSTIC_INFORMATION,
    DIAGNOSTIC_SESSION_CONTROL,
    INPUT_OUTPUT_CONTROL_BY_IDENTIFIER,
    KWP_INPUT_OUTPUT_CONTROL_BY_LOCAL_IDENTIFIER,
    ROUTINE_CONTROL,
)


def _field(value: int, width: int, name: str) -> bytes:
    if not 0 <= value < 1 << (8 * width):
        raise ValueError(f"{name} 0x{value:X} does not fit in {width} byte(s)")
    return value.to_bytes(width, "big")


def _request(sid: int, *fields: bytes) -> str:
    return (bytes([sid]) + b"".join(fields)).hex().upper()


def session_request(mode: int | str) -> str:
    """``10 {mode}``: DiagnosticSessionControl into session ``mode``.

    ``mode`` is the sub-function byte, or the two hex digits a profile and the CLI carry it as
    (``"03"``, ``"C0"``).
    """
    sub = int(mode, 16) if isinstance(mode, str) else mode
    return _request(DIAGNOSTIC_SESSION_CONTROL, _field(sub, 1, "session type"))


def io_control_request(did: int, parameter: int, state: bytes = b"") -> str:
    """``2F {DID} {parameter} {state}``: InputOutputControlByIdentifier (UDS)."""
    return _request(
        INPUT_OUTPUT_CONTROL_BY_IDENTIFIER,
        _field(did, 2, "DID"),
        _field(parameter, 1, "control parameter"),
        state,
    )


def kwp_io_control_request(lid: int, option: bytes) -> str:
    """``30 {LID} {option}``: InputOutputControlByLocalIdentifier (KWP2000).

    The control option is manufacturer-defined (an SSF 14230-3 parameter, or Nissan's function
    and value), so the caller passes its bytes.
    """
    return _request(KWP_INPUT_OUTPUT_CONTROL_BY_LOCAL_IDENTIFIER, _field(lid, 1, "LID"), option)


def routine_control_request(routine_control_type: int, rid: int) -> str:
    """``31 {type} {RID}``: RoutineControl (UDS)."""
    return _request(
        ROUTINE_CONTROL,
        _field(routine_control_type, 1, "routine control type"),
        _field(rid, 2, "routine id"),
    )


def clear_dtc_request(group: int, *, kwp: bool) -> str:
    """``14 {group}``: ClearDiagnosticInformation.

    The group of DTCs is 2 bytes on KWP2000 and 3 bytes on UDS. ``group`` is cut to that
    width, so ``0xFFFFFF`` (all groups) serves both.
    """
    width = 2 if kwp else 3
    group &= (1 << (8 * width)) - 1
    return _request(CLEAR_DIAGNOSTIC_INFORMATION, _field(group, width, "group of DTCs"))
