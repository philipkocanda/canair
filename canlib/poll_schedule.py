"""Per-PID poll scheduling for the live monitor: which PIDs a tick reads, and when.

A dependency-free leaf (the ``monitor`` parser imports its vocabulary at CLI start-up,
so it must not pull in :mod:`canlib.modes`). Two schedules:

- ``dynamic`` (the default) polls each PID at its own profile ``period:`` (the same
  milliseconds the WiCAN AutoPID profile ships) divided by ``period_speedup``,
  falling back to ``--interval`` for a PID that declares none. A fast PID refreshes
  quickly while a slow per-cycle record is read only as often as it can change, so
  bus time goes where it is useful.
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

# The live monitor reads each PID twice as often as its profile period: the period is
# what ships to the AutoPID device (its MQTT cadence), and someone watching the screen
# wants a livelier view than that.
MONITOR_PERIOD_SPEEDUP = 2.0

# A (canonical ECU name, PID code) pair, as the query plans name them.
Key = tuple[str, str]

# Due times computed along different grids (2 s x 5 vs 10 s x 1) differ by float
# rounding; treat anything this close as on time so phase-locked PIDs coincide.
_EPS = 0.001

# Live rate-control bounds (TUI =/-): the fixed interval in seconds, and how many
# _STEP-sized presses the dynamic schedule's poll rate may move either way (counted in
# whole steps, so pressing = then - lands back on exactly the starting rate).
MIN_INTERVAL = 0.1
MAX_INTERVAL = 300.0
MAX_RATE_STEPS = 5
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

    def __init__(
        self,
        schedule: Schedule = DEFAULT_SCHEDULE,
        interval: float = 5.0,
        period_speedup: float = 1.0,
    ):
        if schedule not in SCHEDULES:
            raise ValueError(f"unknown schedule {schedule!r} (expected one of {SCHEDULES})")
        if period_speedup <= 0:
            raise ValueError(f"period_speedup must be positive, not {period_speedup!r}")
        self.schedule: Schedule = schedule
        self.interval = interval
        # Divides every declared profile period (not the --interval fallback, which is
        # already the caller's own choice of seconds).
        self.period_speedup = period_speedup
        self.rate_steps = 0
        # Per-ECU period multiplier set by the slow-ECU backoff (canlib.poll_backoff).
        self._slowdown: dict[str, int] = {}
        # Fixed schedule only: rounds a slowed-down ECU still sits out.
        self._skip: dict[str, int] = {}
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
        if self.schedule == "fixed" and self._sits_out(ecu):
            for entry in plan:
                key = (ecu, entry[0])
                self._period_ms[key] = declared_period_ms(entry[1])
                self._live.add(key)
            return []
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
            if self.schedule == "fixed":
                self._skip[ecu] = self.slowdown(ecu) - 1

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
            return max(0.0, self._now + self.interval / self.rate - now)
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

    # -- the slow-ECU backoff ----------------------------------------------

    def set_slowdown(self, ecu: str, factor: int) -> None:
        """Poll ``ecu`` ``factor`` times less often (1 restores its normal rate)."""
        if factor <= 1:
            self._slowdown.pop(ecu, None)
            self._skip.pop(ecu, None)
        else:
            # Shift the rounds it still sits out by the change, so a slowdown applied
            # straight after a poll takes effect from the next round.
            old = self.slowdown(ecu)
            skip = self._skip.get(ecu, 0) + factor - old
            self._slowdown[ecu] = factor
            self._skip[ecu] = max(0, min(skip, factor - 1))

    def slowdown(self, ecu: str) -> int:
        return self._slowdown.get(ecu, 1)

    def _sits_out(self, ecu: str) -> bool:
        skip = self._skip.get(ecu, 0)
        if skip <= 0:
            return False
        self._skip[ecu] = skip - 1
        return True

    # -- periods and live rate control -------------------------------------

    def period(self, key: Key) -> float:
        """The effective poll period of one PID, in seconds."""
        if self.schedule == "fixed":
            return self.interval * self.slowdown(key[0])
        ms = self._period_ms.get(key)
        base = ms / 1000.0 / self.period_speedup if ms is not None else self.interval
        return base / self.rate * self.slowdown(key[0])

    @property
    def rate(self) -> float:
        """The live multiplier on every PID's poll rate (2.0 = twice as often)."""
        return _STEP**self.rate_steps

    def faster(self) -> str:
        return self._rescale(+1)

    def slower(self) -> str:
        return self._rescale(-1)

    def toggle(self) -> str:
        self.schedule = "fixed" if self.schedule == "dynamic" else "dynamic"
        self._anchor.pop(_ROUND, None)
        self.wake()
        return self.describe()

    def describe(self) -> str:
        """Short human label for the status line, e.g. ``per-PID 1.5× faster`` or ``5.0s``.

        The per-PID label is relative to the schedule's starting rate, so the word
        always says which way the last =/- moved it.
        """
        if self.schedule == "fixed":
            return f"{self.interval:.1f}s"
        if self.rate_steps == 0:
            return "per-PID"
        if self.rate_steps > 0:
            return f"per-PID {self.rate:.3g}× faster"
        return f"per-PID {1 / self.rate:.3g}× slower"

    def _rescale(self, steps: int) -> str:
        """Poll faster (``steps`` > 0) or slower: a rate on the per-PID schedule, a
        shorter or longer interval on the fixed one."""
        if self.schedule == "fixed":
            value = round(self.interval / _STEP**steps, 1)
            self.interval = min(MAX_INTERVAL, max(MIN_INTERVAL, value))
        else:
            value = self.rate_steps + steps
            self.rate_steps = min(MAX_RATE_STEPS, max(-MAX_RATE_STEPS, value))
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
