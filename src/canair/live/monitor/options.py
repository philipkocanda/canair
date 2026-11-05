"""What ``monitor`` needs, as values: the steps it polls and how it records them.

Both monitor paths take this: the ELM327 terminal through ``mode_monitor``, and the raw SLCAN
pipelined client through :mod:`canair.live.monitor.raw_run`. Building it once, in the command, is
what keeps the two from drifting apart on a default. The controller reads its settings from it.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any

from canair.captures.keepmode import KeepMode
from canair.expression.notation import ByteNotation
from canair.live.monitor.schedule import DEFAULT_SCHEDULE, Schedule


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
    # The screen a TTY gets, given the monitor's controller; None polls without one. The CLI
    # passes the Textual app, which sits above this layer.
    interactive: Callable[[Any], Awaitable[None]] | None = None
