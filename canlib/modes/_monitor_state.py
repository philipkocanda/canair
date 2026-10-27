"""Live monitor — vehicle-state auto-suggestion backend.

:class:`MonitorStateSuggester` is the collaborator that owns the monitor's
*state-suggestion* concern: the accumulated ``decoded_values``/``responded``
snapshot, the per-signal/per-ECU clocks that age those inputs, their expiry, and
the evaluation of the profile's ``vehicle_states.yaml`` rules into a suggested
state for the status bar and the save dialog.

Factored out of :class:`canlib.modes.monitor.MonitorController` (mirrors
:class:`canlib.modes.monitor_raw.MonitorRawPoller` and
:class:`canlib.modes._monitor_record.MonitorRecorder`) so the suggestion logic is
a self-contained, independently testable unit rather than another arm of the
controller god object. The controller keeps thin delegating
properties/methods for the tested public surface (``decoded_values``,
``responded``, ``suggested_state``) and the recorder/poll state the suggester
reads via its ``self.c`` back-ref.

The live suggestion answers "what state is the car in NOW", so its inputs must
expire — an unbounded map keeps matching DRIVING off a speed read long after the
car parked. Each input is aged by its own PID's (or ECU's) poll count, not the
wall clock or the global tick count, so a round-robin sweep over a dozen ECUs and
a per-PID schedule that reads a 10 s PID a tenth as often as a 1 s one both expire
a value only after *its own* polls stop refreshing it.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .monitor import MonitorController
    from .multi_batch import EcuFrame

# How many polls a decoded value stays eligible for state auto-suggestion.
# Counted in polls of the signal's own PID rather than seconds so it adapts to
# the poll rate. Three (the same tolerance as
# `transport.stale_cycles_before_reconnect`) rides out a transient dropped frame
# without keeping a genuinely gone signal alive.
STATE_INPUT_STALE_CYCLES = 3


class MonitorStateSuggester:
    """Accumulates decoded inputs and suggests the live vehicle state."""

    def __init__(self, controller: MonitorController):
        self.c = controller
        # Latest decoded {ECU.PARAM: value} + responded ECUs, evaluated against
        # the profile's vehicle_states.yaml rules (lazy-loaded). Both EXPIRE after
        # STATE_INPUT_STALE_CYCLES polls without a refresh: the live suggestion
        # must answer "what is true now", not "what was ever true this run". Each
        # is stamped with its own clock — the poll count of the PID / ECU it came
        # from (see _state_clock).
        self.decoded_values: dict[str, float] = {}
        self.responded: set[str] = set()
        self._value_cycle: dict[str, tuple[str, str | None, int]] = {}
        self._responded_cycle: dict[str, tuple[str, int]] = {}
        self._state_rules: list | None = None

    def _state_clock(self, ecu: str, pid: str | None = None) -> int:
        """The clock a state input is aged by: polls of its own PID (or ECU).

        Falls back to the global cycle count for anything the scheduler has never
        polled (inputs recorded directly, as the tests do).
        """
        sched = self.c.schedule
        n = sched.ecu_ticks(ecu) if pid is None else sched.due_count(ecu, pid)
        return self.c.cycle if n is None else n

    def forget_signal(self, ecu: str, pid: str) -> None:
        """Drop state inputs sourced from one PID (a removed selector).

        Its signals stop being read, so their clock stops too; drop them now
        rather than letting them vouch for a state forever.
        """
        for name, (v_ecu, v_pid, _seen) in list(self._value_cycle.items()):
            if v_ecu == ecu and v_pid == pid:
                del self._value_cycle[name]
                self.decoded_values.pop(name, None)

    def observe(self, new_queries: list[EcuFrame]) -> None:
        """Fold a cycle's freshly-polled payloads into the state-input snapshot."""
        from ..states import collect_values
        from .monitor import _label_ecu

        for ecu_label, pid_results in new_queries:
            ecu = _label_ecu(ecu_label)
            for entry in pid_results:
                values, _ = collect_values([(ecu_label, [entry])])
                self.decoded_values.update(values)
                clock = self._state_clock(ecu, entry["pid"])
                for key in values:
                    self._value_cycle[key] = (ecu, entry["pid"], clock)
            # `responded` is keyed by collect_values' short name; age it by the
            # ECU the row label actually names.
            _values, responded = collect_values([(ecu_label, pid_results)])
            self.responded |= responded
            for short in responded:
                self._responded_cycle[short] = (ecu, self._state_clock(ecu))
        self._expire_state_inputs()

    def _expire_state_inputs(self) -> None:
        """Drop state-suggestion inputs not refreshed in their last few polls.

        ``decoded_values``/``responded`` accumulate across the run, so without
        this they answer "was this ever true", not "is it true now": after a
        drive ends, the last non-zero speed would keep DRIVING matching forever,
        and an ECU that stopped answering would keep vouching for its state.
        Expiring restores the three-valued contract — a signal we no longer read
        goes back to UNKNOWN (abstain) instead of lingering as a stale fact.
        """
        for key, (ecu, pid, seen) in list(self._value_cycle.items()):
            if self._state_clock(ecu, pid) - seen > STATE_INPUT_STALE_CYCLES:
                del self._value_cycle[key]
                self.decoded_values.pop(key, None)
        for short, (ecu, seen) in list(self._responded_cycle.items()):
            if self._state_clock(ecu) - seen > STATE_INPUT_STALE_CYCLES:
                del self._responded_cycle[short]
                self.responded.discard(short)

    def suggested_state(self) -> str | None:
        """Auto-suggest the single, most *specific* vehicle state.

        Several states are true at once by design — driving is genuinely both
        READY and DRIVING — so a one-slot display must not just take the first
        match: it applies the profile's ``implies:`` hierarchy and drops every
        state another match specializes (:func:`canlib.states.most_specific_states`).
        Among states unrelated in that hierarchy, file order still decides.
        """
        from ..states import most_specific_states

        matched = self.suggested_states()
        if not matched:
            return None
        specific = most_specific_states(self._state_rules or [], matched)
        return specific[0] if specific else matched[0]

    def suggested_states(self) -> list[str]:
        """Auto-suggest the vehicle state(s) from the latest decoded values.

        Evaluates the active profile's vehicle_states.yaml rules against the
        accumulated ``decoded_values``/``responded`` snapshot. Returns every
        matching state (a session is naturally composite, e.g.
        ``[READY, PARKED]``), or ``[]`` when no rule matches / the profile
        declares no states.
        """
        from ..states import StatePredicateError, load_states, suggest_states

        if self._state_rules is None:
            try:
                self._state_rules = load_states()
            except StatePredicateError:
                self._state_rules = []
        if not self._state_rules:
            return []
        matched, _false = suggest_states(self._state_rules, self.decoded_values, self.responded)
        return matched

    def state_options(self) -> list[tuple[str, str]]:
        """The profile's ordered ``(state, description)`` vocabulary for the save dialog."""
        from ..states import state_options

        try:
            return state_options()
        except Exception:
            return []
