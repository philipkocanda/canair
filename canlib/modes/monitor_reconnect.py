"""Mid-session reconnect / auto-failover for the live monitor.

Connect-time cross-device fallback lives in :mod:`canlib.transport.fallback`.
This module adds the *mid-session* counterpart: when a monitor session's
transport drops, re-probe the (same-transport) candidate list until one answers,
rebuild the client, and rebind it onto the running
:class:`~canlib.modes.monitor.MonitorController` — preserving its journal and
history so the ``--save`` session simply continues (the gap shows in the
timestamps).

The transport-specific "connect to this candidate" step is supplied by the
caller (``build_elm_reconnector`` in :mod:`canlib.commands._live.connect`,
``build_raw_reconnector`` in :mod:`canlib.modes.raw_monitor`), which reuses the
same build path as the initial connect. The bounded-vs-forever retry policy is
derived from the user's ``--wait`` flag and config
(:func:`canlib.config.reconnect_max_wait`).
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Protocol

if TYPE_CHECKING:
    from ..transport.config import TransportConfig
    from .monitor import MonitorController

# A transport-specific "connect to this candidate, return the ready client".
ConnectFn = Callable[["TransportConfig"], Awaitable[Any]]
NoticeFn = Callable[[str], None]
StopFn = Callable[[], bool]


class Reconnector(Protocol):
    """Callable that re-homes a dropped monitor session; returns True if resumed."""

    async def __call__(
        self,
        controller: MonitorController,
        session_steps: list[dict] | None,
        *,
        stop: StopFn | None = None,
        notice: NoticeFn | None = None,
    ) -> bool: ...


@dataclass(frozen=True)
class ReconnectPolicy:
    """When and how long to retry after a mid-session drop.

    ``forever`` (from ``--wait``) retries until a device answers or the user
    stops; otherwise the attempt is bounded to ``max_wait`` seconds. ``connect_
    timeout`` is the per-device liveness probe, ``poll_interval`` the gap between
    probe rounds.
    """

    forever: bool
    max_wait: float
    connect_timeout: float
    poll_interval: float = 1.0

    def deadline_from(self, now: float) -> float | None:
        """Absolute monotonic deadline for this policy, or ``None`` if forever."""
        return None if self.forever else now + self.max_wait


def reconnect_policy(args) -> ReconnectPolicy:
    """Build a :class:`ReconnectPolicy` from CLI args + user config."""
    from ..config import fallback_settings, reconnect_max_wait

    _, connect_timeout, _ = fallback_settings()
    return ReconnectPolicy(
        forever=bool(getattr(args, "wait", False)),
        max_wait=reconnect_max_wait(),
        connect_timeout=connect_timeout,
    )


class MonitorReconnector:
    """Re-home a dropped monitor session to a reachable same-transport device.

    Probes the (already same-transport-filtered) candidate list until one
    answers, rebuilds the client via ``connect``, and rebinds it onto the
    controller, re-running session setup. The controller — its journal, history,
    and counters — is preserved, so a ``--save`` session continues seamlessly.
    """

    def __init__(
        self,
        candidates: list[TransportConfig],
        connect: ConnectFn,
        policy: ReconnectPolicy,
    ) -> None:
        self._candidates = candidates
        self._connect = connect
        self._policy = policy

    async def __call__(
        self,
        controller: MonitorController,
        session_steps: list[dict] | None,
        *,
        stop: StopFn | None = None,
        notice: NoticeFn | None = None,
    ) -> bool:
        from ..transport.errors import transport_error_types
        from ..transport.fallback import wait_for_reachable

        stopped: StopFn = stop or (lambda: False)
        say: NoticeFn = notice or (lambda _m: None)

        if not self._candidates:
            return False

        # Free the dead socket before re-probing so the OS/device can recycle it.
        await controller.close_client()

        deadline = self._policy.deadline_from(time.monotonic())
        while not stopped():
            # Probe off-thread so a Textual/asyncio event loop stays responsive
            # while we block on socket connects.
            cand = await asyncio.to_thread(
                wait_for_reachable,
                self._candidates,
                connect_timeout=self._policy.connect_timeout,
                poll_interval=self._policy.poll_interval,
                deadline=deadline,
                stop=stopped,
                notice=say,
            )
            if cand is None:
                return False  # deadline passed or user stopped
            try:
                client = await self._connect(cand)
            except transport_error_types() as exc:
                say(f"reconnect to {cand.describe()} failed ({exc}); retrying…")
                # The liveness probe answered but the connect didn't, so
                # `wait_for_reachable` will hand back the same candidate
                # immediately on the next pass — it only consults the deadline
                # when *nothing* is reachable. Without our own deadline check and
                # backoff this became an unbounded, no-sleep hot loop that ignored
                # `transport.reconnect_max_wait` and hammered the device
                # thousands of times a second. Typical trigger: a WiCAN that
                # rebooted into auto_pid — port 80 answers the probe, the data
                # port refuses the connect.
                if not await self._backoff(deadline, stopped):
                    return False
                continue
            controller.rebind(client)
            await controller.setup(session_steps)
            say(f"reconnected to {cand.describe()} — resuming")
            return True
        return False

    async def _backoff(self, deadline: float | None, stopped: StopFn) -> bool:
        """Wait ``poll_interval`` before the next connect attempt.

        Returns False when the attempt should be abandoned — the retry budget
        (``deadline``) is spent or the user stopped. Sleeps in short slices so a
        stop flag set from the TUI/signal handler is honoured promptly.
        """
        if stopped():
            return False
        if deadline is not None and time.monotonic() >= deadline:
            return False
        end = time.monotonic() + self._policy.poll_interval
        if deadline is not None:
            end = min(end, deadline)
        while time.monotonic() < end:
            if stopped():
                return False
            await asyncio.sleep(min(0.1, max(0.0, end - time.monotonic())))
        return not stopped() and not (deadline is not None and time.monotonic() >= deadline)
