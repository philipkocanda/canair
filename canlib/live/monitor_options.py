"""What ``monitor`` needs, as values: the steps it polls and how it records them.

Both monitor paths take this: the ELM327 terminal through ``mode_monitor``, and the raw SLCAN
pipelined client through :mod:`canlib.live.raw_monitor`. Building it once, in the command, is
what keeps the two from drifting apart on a default.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from canlib.captures.keepmode import KeepMode
from canlib.live.poll_schedule import DEFAULT_SCHEDULE, Schedule
from canlib.uds.notation import ByteNotation


@dataclass(frozen=True)
class MonitorOptions:
    """The polled ``query_steps`` (and the ``session_steps`` that open sessions first)."""

    query_steps: list[dict]
    session_steps: list[dict] = field(default_factory=list)
    interval: float = 1.0
    keep_mode: KeepMode = "all"
    keep_n: int | None = None
    save: bool = False
    show_rulers: bool = False
    notation: ByteNotation = ByteNotation.WICAN
    label: str | None = None
    vehicle_states: str | None = None
    notes: str | None = None
    include_static: bool = False
    schedule: Schedule = DEFAULT_SCHEDULE
    backoff: bool = True
    verbose: bool = False

    def mode_kwargs(self) -> dict:
        """The keyword arguments ``mode_monitor`` takes from the options."""
        return {
            "interval": self.interval,
            "session_steps": self.session_steps,
            "keep_mode": self.keep_mode,
            "keep_n": self.keep_n,
            "save": self.save,
            "show_rulers": self.show_rulers,
            "notation": self.notation,
            "label": self.label,
            "vehicle_states": self.vehicle_states,
            "notes": self.notes,
            "include_static": self.include_static,
            "schedule": self.schedule,
            "backoff": self.backoff,
        }
