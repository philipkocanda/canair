"""Per-PID poll scheduling for the live monitor: which PIDs a tick reads, and when.

A dependency-free leaf (the ``monitor`` parser imports its vocabulary at CLI start-up,
so it must not pull in :mod:`canlib.modes`). Two schedules:

- ``dynamic`` (the default) polls each PID at its own profile ``period:`` (the same
  milliseconds the WiCAN AutoPID profile ships), falling back to ``--interval`` for a
  PID that declares none. A fast PID refreshes quickly while a slow per-cycle record
  is read only as often as it can change, so bus time goes where it is useful.
- ``fixed`` polls every PID on every tick, ``--interval`` apart — the behaviour the
  monitor had before per-PID scheduling.

Each PID keeps its own phase anchor and advances along a fixed grid
(``anchor + k * period``), so PIDs whose periods are multiples of one another stay
phase-locked and fall due on the *same* tick — which is what lets a ``multi_did``
ECU keep batching them into one request. A tick that runs late skips the missed grid
points rather than replaying them in a burst.

The scheduler also counts how often each PID and each ECU has been due. That count
is the monitor's per-signal clock: "three cycles without a refresh" has to mean three
of *that* PID's polls, not three ticks of a faster neighbour.
"""

from __future__ import annotations

import math
from typing import Any, Literal, get_args

Schedule = Literal["dynamic", "fixed"]
SCHEDULES: tuple[Schedule, ...] = get_args(Schedule)
DEFAULT_SCHEDULE: Schedule = "dynamic"

# A (canonical ECU name, PID code) pair, as the query plans name them.
Key = tuple[str, str]

# Due times computed along different grids (2 s x 5 vs 10 s x 1) differ by float
# rounding; treat anything this close as on time so phase-locked PIDs coincide.
_EPS = 0.001

# Live rate-control bounds (TUI =/-): the fixed interval in seconds, and the
# dynamic schedule's multiplier on every PID's period.
MIN_INTERVAL = 0.1
MAX_INTERVAL = 300.0
MIN_SCALE = 0.1
MAX_SCALE = 10.0
_STEP = 1.5

# The single phase anchor every PID shares under the fixed schedule.
_ROUND = ("", "")


def declared_period_ms(pid_info: dict[str, Any] | None) -> int | None:
    """A PID's explicit ``period:`` in milliseconds, or None when it declares none."""
    period = (pid_info or {}).get("period")
    if isinstance(period, bool) or not isinstance(period, int) or period <= 0:
        return None
    return period


class PollScheduler:
    """Decides which plan entries are due on a tick, and when the next tick is.

    Drive it once per tick: :meth:`begin_tick`, then :meth:`take_due` for each ECU's
    full query plan, then :meth:`end_tick`. :meth:`seconds_until_due` tells the poll
    loop how long to sleep.
    """

    def __init__(self, schedule: Schedule = DEFAULT_SCHEDULE, interval: float = 5.0):
        if schedule not in SCHEDULES:
            raise ValueError(f"unknown schedule {schedule!r} (expected one of {SCHEDULES})")
        self.schedule: Schedule = schedule
        self.interval = interval
        self.scale = 1.0
        self._anchor: dict[Key, float] = {}
        self._period_ms: dict[Key, int | None] = {}
        self._due_count: dict[Key, int] = {}
        self._ecu_ticks: dict[str, int] = {}
        self._live: set[Key] = set()
        self._now = 0.0
        self._ecus_due: set[str] = set()
        self._woken = True

    # -- the per-tick protocol -------------------------------------------

    def begin_tick(self, now: float) -> None:
        self._now = now
        self._live = set()
        self._ecus_due = set()
        self._woken = False
        if self.schedule == "fixed":
            self._advance(_ROUND, self.interval)

    def take_due(self, ecu: str, plan: list) -> list:
        """The entries of one ECU's full query plan that are due on this tick.

        ``plan`` is ``[(pid_code, pid_info_or_None, unmapped)]`` as built by
        :func:`~canlib.modes.multi_exec.build_query_plan`; order is preserved.
        """
        due = []
        for entry in plan:
            key = (ecu, entry[0])
            self._period_ms[key] = declared_period_ms(entry[1])
            self._live.add(key)
            if self.schedule == "dynamic":
                if not self._is_due(key):
                    continue
                self._advance(key, self.period(key))
            due.append(entry)
            self._due_count[key] = self._due_count.get(key, 0) + 1
            self._ecus_due.add(ecu)
        return due

    def end_tick(self) -> None:
        for ecu in self._ecus_due:
            self._ecu_ticks[ecu] = self._ecu_ticks.get(ecu, 0) + 1

    def seconds_until_due(self, now: float) -> float:
        """How long the poll loop may sleep before something is due (0 = poll now)."""
        if self._woken:
            return 0.0
        if self.schedule == "fixed":
            anchor = self._anchor.get(_ROUND)
            return 0.0 if anchor is None else max(0.0, anchor + self.interval - now)
        if not self._live:
            # Nothing pollable (every selector resolved to no PID): idle at the
            # fallback cadence instead of spinning.
            return max(0.0, self._now + self.interval * self.scale - now)
        nxt = min(self._next_due(key) for key in self._live)
        return max(0.0, nxt - now)

    def wake(self) -> None:
        """Run the next tick now (a PID was just added and should not wait a period)."""
        self._woken = True

    def forget(self, ecu: str, pid: str) -> None:
        """Drop a PID's phase, so re-adding it later polls it straight away."""
        self._anchor.pop((ecu, pid), None)

    # -- the per-signal clock ----------------------------------------------

    def due_count(self, ecu: str, pid: str) -> int | None:
        """How many ticks this PID has been due on, or None if never scheduled."""
        return self._due_count.get((ecu, pid))

    def ecu_ticks(self, ecu: str) -> int | None:
        """How many ticks any of this ECU's PIDs were due on, or None if never."""
        return self._ecu_ticks.get(ecu)

    # -- periods and live rate control -------------------------------------

    def period(self, key: Key) -> float:
        """The effective poll period of one PID, in seconds."""
        if self.schedule == "fixed":
            return self.interval
        ms = self._period_ms.get(key)
        base = ms / 1000.0 if ms is not None else self.interval
        return base * self.scale

    def faster(self) -> str:
        return self._rescale(1 / _STEP)

    def slower(self) -> str:
        return self._rescale(_STEP)

    def toggle(self) -> str:
        self.schedule = "fixed" if self.schedule == "dynamic" else "dynamic"
        self._anchor.pop(_ROUND, None)
        self.wake()
        return self.describe()

    def describe(self) -> str:
        """Short human label for the status line, e.g. ``per-PID ×0.67`` or ``5.0s``."""
        if self.schedule == "fixed":
            return f"{self.interval:.1f}s"
        if math.isclose(self.scale, 1.0):
            return "per-PID"
        return f"per-PID ×{self.scale:g}"

    def _rescale(self, factor: float) -> str:
        if self.schedule == "fixed":
            value = round(self.interval * factor, 1)
            self.interval = min(MAX_INTERVAL, max(MIN_INTERVAL, value))
        else:
            value = round(self.scale * factor, 2)
            self.scale = min(MAX_SCALE, max(MIN_SCALE, value))
        return self.describe()

    # -- the grid --------------------------------------------------------

    def _next_due(self, key: Key) -> float:
        anchor = self._anchor.get(key)
        return -math.inf if anchor is None else anchor + self.period(key)

    def _is_due(self, key: Key) -> bool:
        return self._next_due(key) <= self._now + _EPS

    def _advance(self, key: Key, period: float) -> None:
        """Move ``key``'s anchor to the latest grid point at or before now."""
        anchor = self._anchor.get(key)
        if anchor is None or period <= 0:
            self._anchor[key] = self._now
            return
        due = anchor + period
        if due > self._now + _EPS:
            # Not due yet (a fixed tick that the loop started early, e.g. a wake).
            self._anchor[key] = self._now
            return
        missed = math.floor(max(0.0, self._now + _EPS - due) / period)
        self._anchor[key] = due + missed * period
