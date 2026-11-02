"""The live monitor's link-event recorder: when the recording could not see the car.

Feeds a ``--save`` session's ``link_events`` (:mod:`canlib.captures.link_events`) from
what the monitor already knows: each coherent answer (a payload or an NRC), each poll's
per-ECU outcome, each link failure it hands to the reconnector, the slow-ECU backoff,
pausing, the live PID picker, and each pipe realignment the transport tallies.

**A disconnect or a silent ECU spans the data gap, not the event that revealed it.** It
opens at the last coherent answer before the failure and closes at the first one after it,
so it covers the stale cycles before a liveness trip and a reconnect that succeeded while
nothing answered yet. A pause, a PID taken out of the poll set and a backoff are the
user's or the scheduler's doing, so they open and close at the moment they change.

Every write goes straight to the journal (durably): a crash mid-disconnect is the likeliest
way such a session ends, and the open event then reconciles as lasting to the end.
"""

from __future__ import annotations

import contextlib
from collections.abc import Callable, Set
from dataclasses import dataclass, field
from datetime import datetime
from typing import TYPE_CHECKING, Any

from canlib.captures.link_events import (
    BACKOFF,
    DISCONNECT,
    ECU_SILENT,
    PAUSED,
    RESYNC,
    UNPOLLED,
    DisconnectCause,
    LinkEventKind,
)

if TYPE_CHECKING:
    from canlib.captures.journal import CaptureJournal

JournalFn = Callable[[], "CaptureJournal | None"]
RxFn = Callable[[str], "str | None"]

# Polls of one ECU in a row with no coherent answer, while another ECU answered, before
# it counts as silent. Low enough to catch an ECU falling asleep within seconds, high
# enough that one dropped reply is not a gap.
ECU_SILENT_POLLS = 3

# (kind, ECU name or None, PID or None): one open event per scope.
_Key = tuple[str, "str | None", "str | None"]


@dataclass
class _Open:
    event_id: int
    kind: LinkEventKind
    started: datetime
    fields: dict[str, Any]
    # An ECU-wide ``unpolled``: the PIDs it took out, so re-adding one of them can split it.
    pids: frozenset[str] = field(default_factory=frozenset)


class LinkEventTracker:
    """Turns the monitor's answers, failures and poll changes into journaled link events.

    ``journal`` returns the *current* journal (or None when not recording); it is looked
    up per write because a segment rotation replaces it mid-run. ``rx_of`` resolves an ECU
    name to the response address its captures are filed under (None when it cannot, in
    which case no event is recorded for that ECU: one without an address says nothing).
    """

    def __init__(
        self,
        journal: JournalFn,
        rx_of: RxFn = lambda _ecu: None,
        clock: Callable[[], datetime] = datetime.now,
    ) -> None:
        self._journal = journal
        self._rx_of = rx_of
        self._clock = clock
        self._last_answer: datetime | None = None
        self._ecu_last: dict[str, datetime] = {}
        self._ecu_misses: dict[str, int] = {}
        self._ecu_first_miss: dict[str, datetime] = {}
        self._open: dict[_Key, _Open] = {}
        self._next_id = 1

    # -- queries -------------------------------------------------------------

    @property
    def gap_open(self) -> bool:
        """Whether a disconnect is open."""
        return self.is_open(DISCONNECT)

    def is_open(self, kind: str, ecu: str | None = None, pid: str | None = None) -> bool:
        return (kind, ecu, pid) in self._open

    # -- answers and failures --------------------------------------------------

    def answered(self, acquired_at: float | None = None, ecu: str | None = None) -> None:
        """A coherent answer arrived (``acquired_at`` is its epoch time, else now).

        Closes an open disconnect, and ``ecu``'s silence, when the answer is newer than
        the gap's start, so a reply already in flight when it opened does not end it.
        """
        when = datetime.fromtimestamp(acquired_at) if acquired_at else self._clock()
        if self._last_answer is None or when > self._last_answer:
            self._last_answer = when
        self._close_if_newer((DISCONNECT, None, None), when)
        if ecu is None:
            return
        ecu = ecu.upper()
        last = self._ecu_last.get(ecu)
        if last is None or when > last:
            self._ecu_last[ecu] = when
        self._close_if_newer((ECU_SILENT, ecu, None), when)

    def disconnected(self, cause: DisconnectCause) -> None:
        """The link failed; open a disconnect at the last coherent answer (once)."""
        key = (DISCONNECT, None, None)
        if key not in self._open:
            self._start(key, DISCONNECT, self._last_answer or self._clock(), cause=cause)

    def ecu_polled(self, ecu: str, *, answered: bool, link_ok: bool) -> None:
        """One poll of ``ecu`` finished; ``link_ok`` = another ECU answered this tick.

        A poll in which nothing answered says nothing about one ECU (that is a
        disconnect), so it neither counts towards a silence nor resets one.
        """
        ecu = ecu.upper()
        if answered:
            self._ecu_misses.pop(ecu, None)
            self._ecu_first_miss.pop(ecu, None)
            return
        if not link_ok:
            return
        now = self._clock()
        self._ecu_first_miss.setdefault(ecu, now)
        self._ecu_misses[ecu] = misses = self._ecu_misses.get(ecu, 0) + 1
        key = (ECU_SILENT, ecu, None)
        if misses < ECU_SILENT_POLLS or key in self._open:
            return
        rx = self._rx_of(ecu)
        if rx is not None:
            started = self._ecu_last.get(ecu) or self._ecu_first_miss[ecu]
            self._start(key, ECU_SILENT, started, rx=rx)

    def resynced(self, count: int) -> None:
        """The transport realigned its pipe ``count`` times this poll cycle."""
        if count <= 0:
            return
        when = self._clock()
        self._write(lambda j: j.append_event(RESYNC, when, count=count if count > 1 else None))

    # -- the recorder's own choices ----------------------------------------------

    def paused(self, on: bool) -> None:
        """Polling was paused (``on``) or resumed."""
        key = (PAUSED, None, None)
        if on and key not in self._open:
            self._start(key, PAUSED, self._clock())
        elif not on:
            self._close(key, self._clock())

    def backoff(self, ecu: str, factor: int) -> None:
        """``ecu`` is now polled ``factor`` times less often (1 = back to normal)."""
        ecu = ecu.upper()
        key = (BACKOFF, ecu, None)
        now = self._clock()
        self._close(key, now)
        rx = self._rx_of(ecu)
        if factor > 1 and rx is not None:
            self._start(key, BACKOFF, now, rx=rx, factor=factor)

    def poll_set_changed(self, old: Set[tuple[str, str]], new: Set[tuple[str, str]]) -> None:
        """The polled ``(ECU, PID)`` pairs changed from ``old`` to ``new``.

        A taken-out PID opens an ``unpolled`` for it; an ECU taken out entirely opens one
        for the ECU instead (one event, not one per PID). Re-adding closes them; re-adding
        only some of an ECU's PIDs closes the ECU-wide event and keeps the rest open.
        """
        now = self._clock()
        old = {(e.upper(), p.upper()) for e, p in old}
        new = {(e.upper(), p.upper()) for e, p in new}
        new_ecus = {ecu for ecu, _pid in new}
        for ecu, pid in sorted(new - old):
            self._close((UNPOLLED, ecu, pid), now)
            whole = self._open.get((UNPOLLED, ecu, None))
            if whole is None:
                continue
            self._close((UNPOLLED, ecu, None), now)
            for p in sorted(whole.pids):
                if (ecu, p) not in new:
                    self._open_unpolled(ecu, p, now)
        removed: dict[str, set[str]] = {}
        for ecu, pid in old - new:
            removed.setdefault(ecu, set()).add(pid)
        for ecu, pids in sorted(removed.items()):
            if ecu in new_ecus:
                for pid in sorted(pids):
                    self._open_unpolled(ecu, pid, now)
            elif not self.is_open(UNPOLLED, ecu):
                self._open_unpolled(ecu, None, now, pids=frozenset(pids))

    def _open_unpolled(
        self, ecu: str, pid: str | None, now: datetime, pids: frozenset[str] = frozenset()
    ) -> None:
        key = (UNPOLLED, ecu, pid)
        rx = self._rx_of(ecu)
        if key in self._open or rx is None:
            return
        self._start(key, UNPOLLED, now, rx=rx, pid=pid, pids=pids)

    # -- journal rotation --------------------------------------------------------

    def segment_rotated(self) -> None:
        """A new journal replaced the old one: restate every open event in it.

        The closed segment keeps its copies open-ended (they lasted to that segment's
        end); the new segment's copies start at the rotation.
        """
        now = self._clock()
        still = list(self._open.items())
        self._open.clear()
        for key, ev in still:
            self._start(key, ev.kind, now, pids=ev.pids, **ev.fields)

    # -- plumbing ----------------------------------------------------------------

    def _start(
        self,
        key: _Key,
        kind: LinkEventKind,
        started: datetime,
        *,
        pids: frozenset[str] = frozenset(),
        **fields: Any,
    ) -> None:
        fields = {k: v for k, v in fields.items() if v is not None}
        ev = _Open(self._next_id, kind, started, fields, pids)
        self._next_id += 1
        self._open[key] = ev
        self._write(lambda j: j.append_event(kind, started, event_id=ev.event_id, **fields))

    def _close(self, key: _Key, when: datetime) -> None:
        ev = self._open.pop(key, None)
        if ev is not None:
            self._write(lambda j: j.append_event_end(ev.event_id, when))

    def _close_if_newer(self, key: _Key, when: datetime) -> None:
        ev = self._open.get(key)
        if ev is not None and when > ev.started:
            self._close(key, when)

    def _write(self, action: Callable[[CaptureJournal], None]) -> None:
        journal = self._journal()
        if journal is None:
            return
        # Never let bookkeeping about a failure become a failure of its own.
        with contextlib.suppress(Exception):
            action(journal)
