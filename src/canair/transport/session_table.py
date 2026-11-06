"""Which ECUs hold a diagnostic session, in which mode, and which need a keepalive.

The state :class:`~canair.transport.session_manager.SessionManager` acts on, kept apart from the
I/O so code that has no ``Terminal`` (the raw monitor's poller, which sends from an executor
thread) can keep sessions alive by the same rules. Nothing here sends anything.

A session is *tracked* from the moment its request goes out, whatever the reply: the request
reached the ECU, and keeping it alive is harmless when the ECU refused. Its *mode* is recorded
only when the ECU accepted the request. An ECU that answers TesterPresent with NRC 0x11 has no
TesterPresent in any session (``transport/keepalive.py``), so it is never asked again; its
session stays tracked.
"""

from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass


@dataclass
class _Session:
    refreshed: float
    mode: int | None = None


class SessionTable:
    """Per-ECU session state: when it was last refreshed, its mode, keepalive refusals."""

    def __init__(self, clock: Callable[[], float] | None = None) -> None:
        # Looked up on every call, so a test that patches time.monotonic is obeyed.
        self._clock = clock or (lambda: time.monotonic())
        self._sessions: dict[int, _Session] = {}
        self._no_keepalive: set[int] = set()

    def track(self, tx_id: int, mode: int | None = None, *, at: float | None = None) -> None:
        """Track a session on ``tx_id`` (refreshed now, or ``at``); ``mode`` if it is known."""
        session = self._sessions.get(tx_id)
        refreshed = self._clock() if at is None else at
        if session is None:
            self._sessions[tx_id] = _Session(refreshed, mode)
            return
        session.refreshed = refreshed
        if mode is not None:
            session.mode = mode

    def opened(self, tx_id: int, mode: int, *, accepted: bool) -> None:
        """Record a session request: tracked either way, the mode only when ``accepted``.

        A refusal leaves the ECU in whatever session it was in, so its mode stays.
        """
        self.track(tx_id, mode if accepted else None)

    def mark_active(self, tx_id: int) -> None:
        """Count an answered request as a keepalive: it reset the ECU's session timer too.

        No-op when ``tx_id`` has no tracked session.
        """
        session = self._sessions.get(tx_id)
        if session is not None:
            session.refreshed = self._clock()

    def refused_keepalive(self, tx_id: int) -> bool:
        """Note that ``tx_id`` has no TesterPresent; True the first time only."""
        if tx_id in self._no_keepalive:
            return False
        self._no_keepalive.add(tx_id)
        return True

    def wants_keepalive(self, tx_id: int) -> bool:
        """Is ``tx_id`` a tracked session that still accepts TesterPresent?"""
        return tx_id in self._sessions and tx_id not in self._no_keepalive

    def stale(self, threshold: float, *, now: float | None = None) -> list[int]:
        """The sessions due a keepalive: not refreshed for more than ``threshold`` seconds."""
        now = self._clock() if now is None else now
        return [
            tx
            for tx, session in self._sessions.items()
            if now - session.refreshed > threshold and tx not in self._no_keepalive
        ]

    def refreshed_at(self, tx_id: int) -> float | None:
        session = self._sessions.get(tx_id)
        return session.refreshed if session is not None else None

    def mode(self, tx_id: int) -> int | None:
        """The session mode ``tx_id`` last accepted, or None."""
        session = self._sessions.get(tx_id)
        return session.mode if session is not None else None

    def has(self, tx_id: int) -> bool:
        return tx_id in self._sessions

    def tracked(self) -> list[int]:
        """Every tracked ECU, in the order its session was first tracked."""
        return list(self._sessions)

    def forget(self, tx_id: int) -> None:
        """Stop tracking ``tx_id`` (its session was closed)."""
        self._sessions.pop(tx_id, None)
