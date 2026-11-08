"""The monitor's foreground lane: the user's requests, sent while polling goes on.

A user request (an actuation, a live session switch) must reach the car quickly, without
stopping the poll, and never two at once. :class:`ForegroundLane` runs them in the order they
were asked for, one at a time, on a worker task beside the poll task. The poll does not know
about it: the transport decides, request by request, who goes next.

* On ELM327 every exchange is one ``terminal.transaction()``, an ``asyncio.Lock`` that hands
  the pipe to waiters in FIFO order, so a lane request waits for the one exchange in flight.
* On ``slcan-tcp`` the raw client locks each ECU for one exchange
  (:class:`~canair.transport.raw.isotp_stack.EcuStacks`): a lane request waits for the poll's
  request to the same ECU, if any, and the other ECUs keep polling.

A job waits while the link is down (``ready`` is False): a reconnect replaces the session
manager and the terminal under it, and a request sent into a dead link would only fail.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any

_logger = logging.getLogger(__name__)

# How often a job waiting for the link checks it again, in seconds.
_READY_POLL_S = 0.05


@dataclass
class _Job:
    label: str
    make: Callable[[], Awaitable[Any]]
    done: asyncio.Future = field(default_factory=lambda: asyncio.get_running_loop().create_future())


class ForegroundLane:
    """User jobs, run one at a time and in order, beside the poll."""

    def __init__(self, ready: Callable[[], bool] = lambda: True) -> None:
        self._ready = ready
        self._queue: asyncio.Queue[_Job] | None = None
        self._worker: asyncio.Task | None = None
        #: The label of the job on the bus, or None.
        self.current: str | None = None

    @property
    def waiting(self) -> int:
        """Jobs asked for and not started yet."""
        return self._queue.qsize() if self._queue is not None else 0

    @property
    def idle(self) -> bool:
        return self.current is None and self.waiting == 0

    def submit(self, label: str, make: Callable[[], Awaitable[Any]]) -> asyncio.Future:
        """Queue ``make()`` and return a future of its result (its exception, if it raises).

        Call from the event loop. The worker starts on the first job.
        """
        if self._queue is None:
            self._queue = asyncio.Queue()
        if self._worker is None or self._worker.done():
            self._worker = asyncio.get_running_loop().create_task(self._run(self._queue))
        job = _Job(label, make)
        self._queue.put_nowait(job)
        return job.done

    async def _run(self, queue: asyncio.Queue[_Job]) -> None:
        while True:
            job = await queue.get()
            try:
                while not self._ready():
                    await asyncio.sleep(_READY_POLL_S)
                self.current = job.label
                result = await job.make()
            except asyncio.CancelledError:
                if not job.done.done():
                    job.done.cancel()
                raise
            except Exception as exc:
                _logger.warning("foreground job %s failed: %s", job.label, exc, exc_info=True)
                if not job.done.done():
                    job.done.set_exception(exc)
            else:
                if not job.done.done():
                    job.done.set_result(result)
            finally:
                self.current = None
                queue.task_done()

    async def drain(self) -> None:
        """Wait until every queued job has run."""
        if self._queue is not None and self._worker is not None and not self._worker.done():
            await self._queue.join()

    async def stop(self) -> None:
        """Cancel the job on the bus and every queued one, and stop the worker."""
        if self._worker is not None:
            self._worker.cancel()
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await self._worker
            self._worker = None
        if self._queue is not None:
            while not self._queue.empty():
                job = self._queue.get_nowait()
                job.done.cancel()
            self._queue = None
