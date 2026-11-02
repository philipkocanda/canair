"""The live monitor's link-event recorder: when the recording could not see the car.

Feeds a ``--save`` session's ``link_events`` (:mod:`canlib.captures.link_events`) from
three things the monitor already knows: each coherent answer (a payload or an NRC), each
link failure it hands to the reconnector, and each pipe realignment the transport tallies.

**A disconnect spans the data gap, not the reconnect.** It opens at the last coherent
answer before the failure and closes at the first one after it, so it covers the stale
cycles before a liveness trip and a reconnect that succeeded while nothing answered yet.

Every write goes straight to the journal (durably): a crash mid-disconnect is the likeliest
way such a session ends, and the open event then reconciles as lasting to the end.
"""

from __future__ import annotations

import contextlib
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from typing import TYPE_CHECKING

from canlib.captures.link_events import DISCONNECT, RESYNC, DisconnectCause

if TYPE_CHECKING:
    from canlib.captures.journal import CaptureJournal

JournalFn = Callable[[], "CaptureJournal | None"]


@dataclass
class _OpenGap:
    event_id: int
    cause: DisconnectCause
    started: datetime


class LinkEventTracker:
    """Turns the monitor's answers and failures into journaled link events.

    ``journal`` returns the *current* journal (or None when not recording); it is
    looked up per write because a segment rotation replaces it mid-run.
    """

    def __init__(self, journal: JournalFn, clock: Callable[[], datetime] = datetime.now) -> None:
        self._journal = journal
        self._clock = clock
        self._last_answer: datetime | None = None
        self._open: _OpenGap | None = None
        self._next_id = 1

    @property
    def gap_open(self) -> bool:
        return self._open is not None

    def answered(self, acquired_at: float | None = None) -> None:
        """A coherent answer arrived (``acquired_at`` is its epoch time, else now).

        Closes an open disconnect when the answer is newer than the gap's start, so a
        reply that was already in flight when the link failed does not end it.
        """
        when = datetime.fromtimestamp(acquired_at) if acquired_at else self._clock()
        if self._last_answer is None or when > self._last_answer:
            self._last_answer = when
        gap = self._open
        if gap is not None and when > gap.started:
            self._open = None
            self._write(lambda j: j.append_event_end(gap.event_id, when))

    def disconnected(self, cause: DisconnectCause) -> None:
        """The link failed; open a disconnect at the last coherent answer (once)."""
        if self._open is not None:
            return
        self._start(cause, self._last_answer or self._clock())

    def resynced(self, count: int) -> None:
        """The transport realigned its pipe ``count`` times this poll cycle."""
        if count <= 0:
            return
        when = self._clock()
        self._write(lambda j: j.append_event(RESYNC, when, count=count if count > 1 else None))

    def segment_rotated(self) -> None:
        """A new journal replaced the old one: restate an open disconnect in it.

        The closed segment keeps its copy open-ended (it lasted to that segment's end);
        the new segment's copy starts at the rotation.
        """
        gap = self._open
        if gap is None:
            return
        self._open = None
        self._start(gap.cause, self._clock())

    def _start(self, cause: DisconnectCause, started: datetime) -> None:
        gap = _OpenGap(self._next_id, cause, started)
        self._next_id += 1
        self._open = gap
        self._write(
            lambda j: j.append_event(DISCONNECT, started, event_id=gap.event_id, cause=cause)
        )

    def _write(self, action: Callable[[CaptureJournal], None]) -> None:
        journal = self._journal()
        if journal is None:
            return
        # Never let bookkeeping about a failure become a failure of its own.
        with contextlib.suppress(Exception):
            action(journal)
