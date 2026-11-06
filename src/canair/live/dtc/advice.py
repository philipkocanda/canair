"""What to try when an ECU does not answer a DTC request, or refuses it.

The commonest cause is the car's power state: an ECU that is switched off in the current
state (ignition off, ACC only, not READY) gives no answer at all, and one that is awake may
still refuse a clear with ``conditionsNotCorrect`` while, for example, the drive system is
on. The profile says in which states each ECU answers (its ``vehicle_states:``), so the
hints name them instead of guessing.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from canair.ecus.names import ecu_name
from canair.ecus.pids import load_pids
from canair.profile.model import Profile
from canair.states.selectors import ecu_states
from canair.states.tokens import ALL_STATE, join_states
from canair.uds.nrc import (
    KWP_NRC_SERVICE_NOT_SUPPORTED_IN_ACTIVE_DIAGNOSTIC_SESSION,
    NRC_BUSY_REPEAT_REQUEST,
    NRC_CONDITIONS_NOT_CORRECT,
    NRC_REQUEST_OUT_OF_RANGE,
    NRC_SECURITY_ACCESS_DENIED,
    NRC_SERVICE_NOT_SUPPORTED,
    NRC_SERVICE_NOT_SUPPORTED_IN_ACTIVE_SESSION,
)
from canair.uds.response import CAT_NO_DATA, classify_error

_NEEDS_SESSION = frozenset(
    {
        NRC_SERVICE_NOT_SUPPORTED_IN_ACTIVE_SESSION,
        KWP_NRC_SERVICE_NOT_SUPPORTED_IN_ACTIVE_DIAGNOSTIC_SESSION,
    }
)


def answering_states(
    tx_id: int, *, profile: Profile, pids_data: Mapping[str, Any] | None = None
) -> list[str]:
    """The states the profile says the ECU ``tx_id`` answers in (empty when it says none)."""
    data = pids_data if pids_data is not None else load_pids(profile)
    name = ecu_name(tx_id, profile=profile)
    return ecu_states((data.get("ecus") or {}).get(name), profile)


def power_state_note(
    tx_id: int, *, profile: Profile, pids_data: Mapping[str, Any] | None = None
) -> str | None:
    """``BMS answers only in READY, CHARGING …``, or None when the profile names no limit."""
    states = answering_states(tx_id, profile=profile, pids_data=pids_data)
    if not states or ALL_STATE in states:
        return None
    return (
        f"{ecu_name(tx_id, profile=profile)} answers only in {join_states(states)} "
        "(its vehicle_states in the profile)"
    )


def power_state_hint(
    tx_id: int, *, profile: Profile, pids_data: Mapping[str, Any] | None = None
) -> str | None:
    """Which power state the ECU needs, or None when the profile says it always answers."""
    if note := power_state_note(tx_id, profile=profile, pids_data=pids_data):
        return f"{note}: put the car in one of these states, then retry"
    if ALL_STATE in answering_states(tx_id, profile=profile, pids_data=pids_data):
        return None
    return (
        "the car may be in a power state where the ECU is off: switch the ignition on, then "
        "retry (`canair states` lists the profile's states)"
    )


def no_response_hints(
    tx_id: int,
    result: Mapping[str, Any],
    *,
    profile: Profile,
    pids_data: Mapping[str, Any] | None = None,
    wake: bool = False,
) -> list[str]:
    """Hints when ``result`` (a response, or a DTC read's result) says the ECU gave no
    answer: its power state first, then a wake. None for any other outcome."""
    if result.get("ok") or result.get("nrc") is not None:
        return []
    if classify_error(result.get("error")) != CAT_NO_DATA:
        return []
    hints = [h for h in [power_state_hint(tx_id, profile=profile, pids_data=pids_data)] if h]
    if not wake:
        hints.append("if the ECU sleeps in this state, retry with --wake")
    return hints


def clear_nrc_hints(
    nrc: int,
    tx_id: int,
    *,
    profile: Profile,
    pids_data: Mapping[str, Any] | None = None,
    session: bool = False,
) -> list[str]:
    """Hints for a negative response to ClearDiagnosticInformation (``0x14``)."""
    if nrc == NRC_CONDITIONS_NOT_CORRECT:
        hints = [
            "the ECU refuses to clear in the current conditions, often the power state: many "
            "ECUs clear only with the ignition on and the engine or drive system off"
        ]
        if note := power_state_note(tx_id, profile=profile, pids_data=pids_data):
            hints.append(note)
        return hints
    if nrc in _NEEDS_SESSION and not session:
        return ["the ECU clears only in another session: retry with --session"]
    if nrc == NRC_SECURITY_ACCESS_DENIED:
        return ["the ECU wants SecurityAccess (0x27) before it clears; canair does not unlock it"]
    if nrc == NRC_SERVICE_NOT_SUPPORTED:
        return ["the ECU does not support ClearDiagnosticInformation (0x14)"]
    if nrc == NRC_REQUEST_OUT_OF_RANGE:
        return ["the ECU does not accept this group of DTCs: try another --group"]
    if nrc == NRC_BUSY_REPEAT_REQUEST:
        return ["the ECU is busy: retry in a moment"]
    return []
