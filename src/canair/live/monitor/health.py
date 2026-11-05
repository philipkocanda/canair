"""Is the link answering? The monitor's per-tick health: answers, backoff and liveness.

:class:`MonitorHealth` takes what a poll tick cost (``poll_costs``, left by the poll
backends) and decides three things from it:

* which polled ECUs answered, for the ``--save`` session's ``link_events``;
* which PIDs to slow down or restore (the :class:`~canair.live.monitor.backoff.PollBackoff`
  verdicts, applied to the :class:`~canair.live.monitor.schedule.PollScheduler`);
* whether the session is alive. A pipe that is open but useless raises nothing, so a run
  of ticks in which nothing answered coherently hands the session to the reconnector.

A reconnect resets the backoff and the scheduler's slowdown together
(:meth:`MonitorHealth.reset_backoff`, called from ``MonitorController.rebind``).
Marking the link down is not done here: the owner passes ``mark_disconnected``, the one
way to do it, which also tells the link-event tracker.
"""

from __future__ import annotations

import logging
from collections.abc import Callable, Sequence

from canair.captures.link_events import DisconnectCause
from canair.config import transport as config_transport
from canair.core.eventlog import log_event
from canair.expression.results import ResultEntry

from .backoff import PollBackoff, PollCost, describe_slowdown, slowdown_log_message
from .link_events import LinkEventTracker
from .schedule import Key, PollScheduler


def is_nrc(entry: ResultEntry) -> bool:
    """A negative response: the result's builder sets its ``nrc``."""
    return entry.get("nrc") is not None


def answered(entry: ResultEntry) -> bool:
    """A coherent reply: a payload, or a negative response (a prompt refusal)."""
    return bool(entry.get("raw_hex")) or is_nrc(entry)


def label_ecu(label: str) -> str:
    """The ECU name a monitor row label carries (``"BMS (0x7E4)"`` -> ``"BMS"``)."""
    return label.rsplit(" (", 1)[0].upper()


def pid_costs(ecu: str, entries: Sequence[ResultEntry]) -> dict[Key, PollCost]:
    """Each PID's cost behind one ECU step's ``entries``, keyed ``(ecu, pid)``.

    Every entry is one request for its PID, timed by the ``request_s`` the executor
    stamps on it (keepalives already left out). A multi-DID batch's members all carry
    their one request's time: each was held up that long, so the batch counts as one
    request per PID rather than being divided among them, which would hide it.
    """
    return {
        (ecu, e["pid"]): PollCost(
            reads=1, seconds=e.get("request_s", 0.0), answered=int(answered(e))
        )
        for e in entries
    }


class MonitorHealth:
    """Answers, backoff and liveness for one monitor run."""

    def __init__(
        self,
        schedule: PollScheduler,
        backoff: PollBackoff,
        link_events: LinkEventTracker,
        *,
        mark_disconnected: Callable[[DisconnectCause], None],
        transport: Callable[[], str | None],
    ):
        self.schedule = schedule
        self.backoff = backoff
        self.link_events = link_events
        self._mark_disconnected = mark_disconnected
        self._transport = transport
        # What polling each PID cost on the last tick, left by the poll backends.
        self.poll_costs: dict[Key, PollCost] = {}
        # Poll cycles in which not one PID answered coherently (no payload and no
        # negative response — every row carried forward as stale). A
        # desynced-but-open pipe raises nothing, so this count is the only thing
        # that can tell a live-but-useless link from a healthy one; at
        # `config.stale_cycles_before_reconnect()` it marks the link down and lets
        # the reconnector rebuild the client. An NRC counts as answered: a negative
        # response proves the request reached the ECU and its reply came back in the
        # right slot, which is a healthy pipe refusing, not a broken one.
        #
        # Counted per ECU (`unanswered`: polls of that ECU since anything last
        # answered) and reported as the minimum over the polled ECUs, so a fast PID
        # on a sleeping ECU cannot escalate on its own while a slower ECU is still
        # answering between its ticks. On the fixed schedule every ECU is polled
        # every cycle, so this is simply consecutive dead cycles.
        self.dead_cycles = 0
        self.unanswered: dict[str, int] = {}
        self.cycle_answered = False

    def begin_tick(self) -> None:
        """Forget the last tick's costs and answers."""
        self.cycle_answered = False
        self.poll_costs = {}

    def observe_ecu_answers(self) -> None:
        """Tell the link-event recorder which polled ECUs answered this tick.

        ``link_ok`` is whether anything answered at all: an ECU that misses while the
        others answer is going quiet on its own, one that misses with them is part of a
        disconnect, which the liveness check records instead.
        """
        replied: dict[str, bool] = {}
        for (ecu, _pid), cost in self.poll_costs.items():
            if cost.reads:
                replied[ecu] = replied.get(ecu, False) or cost.answered > 0
        for ecu, ok in replied.items():
            self.link_events.ecu_polled(ecu, answered=ok, link_ok=self.cycle_answered)

    def apply_backoff(self) -> None:
        """Slow down (or restore) each PID this tick's costs say is holding up the poll."""
        for key, cost in self.poll_costs.items():
            factor = self.backoff.observe(key, cost)
            if factor is None:
                continue
            ecu, pid = key
            self.schedule.set_slowdown(key, factor)
            self.link_events.backoff(ecu, factor, pid=pid)
            detail = slowdown_log_message(f"{ecu} {pid}", factor, self.backoff.reason(key))
            log_event("config", detail, level=logging.INFO, transport=self._transport())

    def reset_backoff(self) -> None:
        """Restore every slowed-down PID to its normal poll rate.

        Clears both halves of the slowdown: the backoff's history and the scheduler's
        factor. Resetting only the backoff would leave the scheduler stretching a PID
        the backoff no longer tracks, so nothing would ever restore it.
        """
        slowed = set(self.backoff.slowed()) | set(self.schedule.slowed())
        for key in sorted(slowed):
            ecu, pid = key
            self.schedule.set_slowdown(key, 1)
            self.link_events.backoff(ecu, 1, pid=pid)
        self.backoff.reset()
        if slowed:
            names = ", ".join(f"{ecu} {pid}" for ecu, pid in sorted(slowed))
            log_event(
                "config",
                f"poll rates restored after reconnecting: {names}",
                level=logging.INFO,
                transport=self._transport(),
            )

    def check_liveness(self, polled: set[str], live: set[str]) -> None:
        """Escalate an all-stale run to a reconnect.

        A transport error raises and is caught by the poll loop, but a pipe that
        is *open and useless* — every reply landing one slot behind its request —
        raises nothing, so the link would never be marked down and the session
        would sit there showing carried-forward values forever. That is the bug
        this guards: count polls where nothing answered, and hand the session to
        the reconnector once it is clearly not recovering on its own.

        ``polled`` are the ECUs read this tick, ``live`` every ECU on screen. It
        escalates once *every* live ECU has gone unanswered for the threshold, so
        a tick that only reached a sleeping ECU is not held against a slower one
        that is still answering between its own ticks.
        """
        if self.cycle_answered or not polled:
            self.unanswered.clear()
            self.dead_cycles = 0
            return
        for ecu in polled:
            self.unanswered[ecu] = self.unanswered.get(ecu, 0) + 1
        self.dead_cycles = min(self.unanswered.get(ecu, 0) for ecu in live | polled)
        limit = config_transport.stale_cycles_before_reconnect()
        if limit <= 0 or self.dead_cycles < limit:
            return
        log_event(
            "stale",
            f"no coherent response for {self.dead_cycles} poll cycles - reconnecting",
            transport=self._transport(),
        )
        self.unanswered.clear()
        self.dead_cycles = 0
        self._mark_disconnected("stale")

    def pid_notes(self, last_queries: Sequence[tuple[str, Sequence[ResultEntry]]]) -> dict:
        """Row note per slowed-down PID, keyed by its ``(ECU label, PID)`` display key."""
        notes: dict[tuple[str, str], str] = {}
        for label, rows in last_queries:
            ecu = label_ecu(label)
            for row in rows:
                key = (ecu, row["pid"])
                factor = self.schedule.slowdown(key)
                if factor > 1:
                    notes[(label, row["pid"])] = describe_slowdown(factor, self.backoff.reason(key))
        return notes
