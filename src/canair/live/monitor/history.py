"""The monitor's on-screen payload history: one row per run of identical polls.

Recording keeps every poll (``--keep-all``, the default), but a stationary signal
answers with the same payload poll after poll, so the live view collapses each
run of identical payloads into one row carrying a poll count. That keeps the view
readable and bounds what each render has to walk, whatever the recording keeps.
"""

from __future__ import annotations

from typing import NamedTuple


class HistoryRun(NamedTuple):
    """A run of consecutive identical payloads of one PID."""

    payload: str
    time: str  # the run's first poll ("" when not yet timestamped)
    polls: int = 1


PayloadHistory = dict[tuple[str, str], list[HistoryRun]]
"""``(ecu_label, pid)`` → that PID's runs, oldest first."""


def extend_runs(runs: list[HistoryRun], payload: str, time: str, *, limit: int | None) -> None:
    """Add one poll to ``runs``, keeping at most ``limit`` newest runs when given."""
    if runs and runs[-1].payload == payload:
        runs[-1] = runs[-1]._replace(polls=runs[-1].polls + 1)
        return
    runs.append(HistoryRun(payload, time))
    if limit and len(runs) > limit:
        del runs[:-limit]
