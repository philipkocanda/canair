"""Live monitor — PID-selector management backend.

:class:`MonitorSelectors` is the collaborator that owns the monitor's live
*PID-selection* concern (the TUI 'p' picker): enumerating the profile's ECUs and
their defined PIDs, reporting which selectors are currently polled (explicit PID
filters vs whole-ECU sweeps), and toggling a PID selector on/off mid-run.

Factored out of :class:`canlib.modes.monitor.MonitorController` (mirrors
:class:`canlib.modes._monitor_sessions.MonitorSessions` and
:class:`canlib.modes._monitor_state.MonitorStateSuggester`) so the picker logic is
a self-contained unit rather than another arm of the controller god object. The
controller keeps thin delegating methods for the TUI surface and holds the
``query_steps``/schedule/caches the selectors mutate via their ``self.c`` back-ref.

Mutating ``query_steps`` mid-poll is safe — ``_poll_elm``/``_poll_raw`` both
iterate a fresh ``for step in self.query_steps`` each cycle, so an add/remove takes
effect on the very next cycle with no separate "apply". See
``_monitor_tui.PidPickerScreen``.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .monitor import MonitorController


class MonitorSelectors:
    """Enumerate, report and toggle the monitor's live PID selectors."""

    def __init__(self, controller: MonitorController):
        self.c = controller

    def available_ecus(self) -> list[str]:
        """Every ECU known in the active profile, for the PID-picker."""
        idx = self.c._ecu_index
        return sorted(idx.keys()) if idx else []

    def available_pids(self, ecu_name: str) -> list[tuple[str, str]]:
        """(pid_code, description) pairs for one ECU's defined PIDs.

        The description is a comma-joined list of the PID's decoded parameter
        names (empty for an undecoded/placeholder PID) — enough for the picker
        to show what a selector actually reads without opening the YAML.
        """
        info = (self.c._ecu_index or {}).get(ecu_name.upper())
        if not info:
            return []
        out: list[tuple[str, str]] = []
        for code, pdef in sorted(info["pids"].items()):
            params = pdef.get("parameters") or {}
            out.append((code, ", ".join(params.keys())))
        return out

    def active_selectors(self) -> set[tuple[str, str]]:
        """(ECU, PID) pairs currently polled with an explicit PID filter."""
        out: set[tuple[str, str]] = set()
        for step in self.c.query_steps:
            ecu = step["ecu"].upper()
            for pid in step.get("pids") or []:
                out.add((ecu, pid.upper()))
        return out

    def swept_ecus(self) -> set[str]:
        """ECUs currently polled with NO PID filter (a bare whole-ECU sweep).

        A PID under a swept ECU is already covered by that sweep — the picker
        marks it distinctly and can't "remove" it individually (only removing
        the whole-ECU step would narrow the sweep, which isn't what a single
        PID toggle means).
        """
        return {step["ecu"].upper() for step in self.c.query_steps if not step.get("pids")}

    def add(self, ecu_name: str, pid_code: str) -> None:
        """Start polling one PID on ``ecu_name`` (TUI PID-picker toggle-on).

        No-ops if the ECU is already swept whole (nothing to add) or the PID is
        already an explicit selector. Appends a new query step if the ECU isn't
        polled at all yet.
        """
        ecu = ecu_name.upper()
        pid = pid_code.upper()
        if ecu in self.swept_ecus():
            return
        self.c.schedule.wake()
        for step in self.c.query_steps:
            if step["ecu"].upper() == ecu:
                pids = step.setdefault("pids", [])
                if pid not in [p.upper() for p in pids]:
                    pids.append(pid)
                return
        self.c.query_steps.append({"type": "query", "ecu": ecu, "pids": [pid]})

    def remove(self, ecu_name: str, pid_code: str) -> None:
        """Stop polling one PID on ``ecu_name`` (TUI PID-picker toggle-off).

        Drops the whole query step once its PID list empties, and purges the
        removed key from the last-good/last-shown caches so a re-added PID
        starts fresh instead of instantly reusing a now-stale cached value.
        """
        from .monitor import _label_ecu

        c = self.c
        ecu = ecu_name.upper()
        pid = pid_code.upper()
        for step in list(c.query_steps):
            if step["ecu"].upper() != ecu:
                continue
            pids = step.get("pids") or []
            if pid not in [p.upper() for p in pids]:
                continue
            step["pids"] = [p for p in pids if p.upper() != pid]
            if not step["pids"]:
                c.query_steps.remove(step)
            for cache in (c._last_good, c._last_shown):
                for key in [k for k in cache if k[1] == pid and _label_ecu(k[0]) == ecu]:
                    del cache[key]
            c.schedule.forget(ecu, pid)
            c.state.forget_signal(ecu, pid)
            return
