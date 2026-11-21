"""Services whose read or undo requests are safe but other subfunctions change state."""

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Final

from canair.uds.services import (
    ACCESS_TIMING_PARAMETER,
    CONTROL_DTC_SETTING,
    READ_DATA_BY_PERIODIC_IDENTIFIER,
    RESPONSE_ON_EVENT,
    service_name,
)
from canair.uds.subfunctions import (
    DtcSettingType,
    PeriodicTransmissionMode,
    ResponseOnEventType,
    TimingParameterAccessType,
)


@dataclass(frozen=True)
class GuardedService:
    """The read/undo subfunctions and mask for a state-changing service."""

    effect: str
    allowed: frozenset[int]
    mask: int = 0x7F


GUARDED_SERVICES: Final[Mapping[int, GuardedService]] = {
    READ_DATA_BY_PERIODIC_IDENTIFIER: GuardedService(
        "the ECU sends replies on its own until told to stop, which can desync the link",
        frozenset({PeriodicTransmissionMode.STOP_SENDING}),
        mask=0xFF,
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


def check_guarded_service(sid: int, sub: int | None) -> str | None:
    """Explain why a subfunction is refused; missing subfunctions are refused."""
    guard = GUARDED_SERVICES[sid]
    if sub is not None and (sub & guard.mask) in guard.allowed:
        return None
    name = service_name(sid) or f"service 0x{sid:02X}"
    which = f"0x{sub:02X}" if sub is not None else "with no sub-function"
    return (
        f"BLOCKED: {name} {which} -- {guard.effect}; re-run with --unsafe if you are "
        "certain it is safe"
    )
