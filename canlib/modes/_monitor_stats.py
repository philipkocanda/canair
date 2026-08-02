"""Live monitor — per-signal value-range accumulation.

:class:`ParamStats` tracks, for every ``(ecu_label, pid)`` polled during a monitor
run, the decoded value *range* of each parameter across all cycles seen so far —
numeric min/max plus the set of distinct non-numeric (enum/flag/text) values. It
backs the monitor's "ranges" view mode, which shows the full captured span of
each signal the way ``canair investigate``/``decode`` report a value range,
without needing the on-screen keep-history (which the default keep-mode trims).

Pure in-memory accumulation over the decoded parameter rows the poller already
produces (:data:`canlib.decoding.ParamRow`); no I/O, no CAN access — a
self-contained, independently testable collaborator (mirroring
:class:`canlib.modes._monitor_record.MonitorRecorder`).
"""

from __future__ import annotations

from typing import TYPE_CHECKING, TypedDict

if TYPE_CHECKING:
    from ..decoding import ParamRow

# Cap on distinct non-numeric values remembered per parameter, so a noisy text
# signal can't grow the accumulator without bound. Beyond this the range view
# shows the first few plus a "+N more" tail.
_MAX_DISTINCT = 12


class SignalStat(TypedDict):
    """Accumulated range of one parameter across a monitor run."""

    unit: str
    verified: bool
    n: int  # number of (non-error) samples folded in
    min: float | None  # numeric minimum seen (None if never numeric)
    max: float | None  # numeric maximum seen
    values: list[str]  # distinct non-numeric value labels, insertion-ordered
    overflow: int  # distinct non-numeric values dropped past _MAX_DISTINCT


class ParamStats:
    """Accumulates per-``(ecu, pid)`` parameter value ranges across a run."""

    def __init__(self) -> None:
        self._stats: dict[tuple[str, str], dict[str, SignalStat]] = {}

    def observe(self, key: tuple[str, str], params: list[ParamRow]) -> None:
        """Fold one PID's decoded parameter rows into the running ranges.

        Error rows (a decode failure) are skipped; a numeric value updates the
        min/max, any other value is tracked as a distinct label.
        """
        for row in params:
            name, value, unit, _expr, perr, verified = row[:6]
            if perr:
                continue
            self._update(key, name, value, unit, bool(verified))

    def _update(
        self,
        key: tuple[str, str],
        name: str,
        value: object,
        unit: str,
        verified: bool,
    ) -> None:
        per_pid = self._stats.setdefault(key, {})
        stat = per_pid.get(name)
        if stat is None:
            stat = SignalStat(
                unit=unit,
                verified=verified,
                n=0,
                min=None,
                max=None,
                values=[],
                overflow=0,
            )
            per_pid[name] = stat
        # Keep unit/verified fresh (a live edit can flip verified mid-run).
        stat["unit"] = unit
        stat["verified"] = verified
        stat["n"] += 1
        # bool is an int subclass but is a flag, not a measurement — treat it as
        # a distinct label so True/False don't collapse into a 0..1 numeric range.
        if isinstance(value, bool):
            self._add_distinct(stat, "true" if value else "false")
        elif isinstance(value, (int, float)):
            stat["min"] = value if stat["min"] is None else min(stat["min"], value)
            stat["max"] = value if stat["max"] is None else max(stat["max"], value)
        elif value is not None:
            self._add_distinct(stat, str(value))

    @staticmethod
    def _add_distinct(stat: SignalStat, label: str) -> None:
        if label in stat["values"]:
            return
        if len(stat["values"]) >= _MAX_DISTINCT:
            stat["overflow"] += 1
            return
        stat["values"].append(label)

    def for_pid(self, key: tuple[str, str]) -> dict[str, SignalStat]:
        """The accumulated ranges for one ``(ecu, pid)`` (empty if none seen)."""
        return self._stats.get(key, {})
