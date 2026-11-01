"""Live monitor — PID-selector management backend.

:class:`MonitorSelectors` is the collaborator that owns the monitor's live
*PID-selection* concern (the TUI ``p`` picker): offering the profile's ECUs, PIDs
and saved groups opened on what is polled now, and swapping in a new selection
mid-run. The picker itself is the same screen a bare ``canair monitor`` shows
(:mod:`canlib.modes._monitor_select_tui`), and the pick ↔ selector mapping is the
same pure logic (:mod:`canlib.modes.monitor_select`).

Factored out of :class:`canlib.modes.monitor.MonitorController` (mirrors
:class:`canlib.modes._monitor_sessions.MonitorSessions` and
:class:`canlib.modes._monitor_state.MonitorStateSuggester`) so the picker logic is
a self-contained unit rather than another arm of the controller god object. The
controller keeps thin delegating methods for the TUI surface and holds the
``query_steps``/schedule/caches the selectors mutate via their ``self.c`` back-ref.

Mutating ``query_steps`` mid-poll is safe — ``_poll_elm``/``_poll_raw`` both
iterate a fresh ``for step in self.query_steps`` each cycle, so a new selection
takes effect on the very next cycle with no restart.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import TYPE_CHECKING

from .monitor_select import (
    EcuChoice,
    GroupChoice,
    build_choices,
    build_group_choices,
    current_selection,
    selection_steps,
)

if TYPE_CHECKING:
    from .monitor import MonitorController


@dataclass(frozen=True)
class PickerSeed:
    """Everything the live picker opens with."""

    choices: list[EcuChoice]
    groups: list[GroupChoice]
    current: dict[str, list[str]]
    include_static: bool


class MonitorSelectors:
    """Offer and apply the monitor's live ECU/PID selection."""

    def __init__(self, controller: MonitorController):
        self.c = controller

    def available_ecus(self) -> list[str]:
        """Every ECU known in the active profile (the session-type picker's list)."""
        idx = self.c._ecu_index
        return sorted(idx.keys()) if idx else []

    def _choices(self) -> list[EcuChoice]:
        return build_choices(self.c._ecu_index or {})

    def picker_seed(self) -> PickerSeed:
        """The picker's choices and groups, opened on what is polled now."""
        from canlib.ecus.ecu_groups import load_groups

        choices = self._choices()
        include_static = self.c.include_static
        return PickerSeed(
            choices=choices,
            groups=build_group_choices(load_groups(), choices, include_static=include_static),
            current=current_selection(choices, self.c.query_steps, include_static),
            include_static=include_static,
        )

    def apply(self, selected: Mapping[str, Sequence[str]]) -> list[str]:
        """Poll exactly ``selected`` (``{ecu: [pid, ...]}``) from the next cycle on.

        Steps the picker cannot show are kept as they are: an ECU outside its
        choices, and a PID filter on a kept ECU that names no defined PID (a raw
        request). A kept ECU stays where it was in the poll order and a new one
        is appended, so the display does not reshuffle. Every PID no longer
        polled is forgotten (cached values, schedule, state evidence), so
        re-adding it later starts fresh. Returns the new selectors, for display.
        """
        c = self.c
        choices = self._choices()
        by_name = {e.name: e for e in choices}
        wanted = dict(selection_steps(choices, selected, include_static=c.include_static))

        old_pairs = self._polled_pairs(c.query_steps)
        steps: list[dict] = []
        placed: set[str] = set()
        for step in c.query_steps:
            ecu = str(step["ecu"]).upper()
            choice = by_name.get(ecu)
            if choice is None:
                steps.append(step)
                continue
            if ecu not in wanted or ecu in placed:
                continue
            _picked, raw = choice.resolve(step.get("pids") or [], c.include_static)
            pids = wanted[ecu]
            if raw:  # a bare sweep would drop the raw requests, so spell it out
                pids = (pids or choice.default_pids(c.include_static)) + raw
            steps.append({**step, "pids": pids})
            placed.add(ecu)
        for ecu, pids in wanted.items():
            if ecu not in placed:
                steps.append({"type": "query", "ecu": ecu, "pids": list(pids)})

        new_pairs = self._polled_pairs(steps)
        c.query_steps[:] = steps
        for ecu, pid in old_pairs - new_pairs:
            self._forget(ecu, pid)
        if new_pairs - old_pairs:
            c.schedule.wake()
        return [f"{s['ecu']}:{','.join(s['pids'])}" if s.get("pids") else s["ecu"] for s in steps]

    def _polled_pairs(self, steps: Sequence[Mapping]) -> set[tuple[str, str]]:
        """``(ECU, PID)`` for every request ``steps`` polls, raw ones included."""
        from .multi_exec import build_query_plan

        out: set[tuple[str, str]] = set()
        for step in steps:
            ecu = str(step["ecu"]).upper()
            info = (self.c._ecu_index or {}).get(ecu)
            if not info:
                continue
            plan = build_query_plan(
                info, step.get("pids") or [], quiet=True, include_static=self.c.include_static
            )
            out.update((ecu, code.upper()) for code, _info, _unmapped in plan or [])
        return out

    def _forget(self, ecu: str, pid: str) -> None:
        from .monitor import _label_ecu

        c = self.c
        for cache in (c._last_good, c._last_shown):
            for key in [k for k in cache if k[1] == pid and _label_ecu(k[0]) == ecu]:
                del cache[key]
        c.schedule.forget(ecu, pid)
        c.state.forget_signal(ecu, pid)
