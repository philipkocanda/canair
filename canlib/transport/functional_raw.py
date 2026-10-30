"""The raw-CAN (``slcan-tcp``) side of a functional request: send one frame, hear everyone.

canair runs ISO-TP itself on this path, so it sees every frame and must send each
multi-frame responder its flow control, on that ECU's *physical* request id. The
AVN times out on flow control sent to ``0x7DF``. The listening is a callback on the
terminal's existing python-can ``Notifier``, so the per-ECU ISO-TP stacks keep
working alongside it; the decisions are :class:`~canlib.transport.functional.
FunctionalCollector`'s.
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import Callable

import can

from .functional import (
    DEFAULT_WINDOW_S,
    FunctionalCollector,
    FunctionalResult,
    flow_control_frame,
    single_frame,
)


async def raw_functional_exchange(
    bus: can.BusABC,
    notifier: can.Notifier,
    *,
    tx_id: int,
    request: bytes,
    padding: int,
    stmin: int = 0,
    window: float = DEFAULT_WINDOW_S,
    flow_control_to: Callable[[int], int | None],
) -> FunctionalResult:
    """Broadcast ``request`` on ``tx_id`` and collect every reply.

    ``flow_control_to`` maps a responder's id to where its flow control goes, or
    None when something else answers it (an ISO-TP stack canair already runs for
    that ECU sends its own).
    """
    loop = asyncio.get_running_loop()
    queue: asyncio.Queue[can.Message] = asyncio.Queue()

    def on_frame(msg: can.Message) -> None:  # the notifier's thread
        loop.call_soon_threadsafe(queue.put_nowait, msg)

    extended = tx_id > 0x7FF
    notifier.add_listener(on_frame)
    try:
        started = time.monotonic()
        bus.send(
            can.Message(
                arbitration_id=tx_id,
                data=single_frame(request, padding),
                is_extended_id=extended,
            )
        )
        collector = FunctionalCollector(tx_id, request, started=started, window=window)
        while not collector.done(now := time.monotonic()):
            try:
                msg = await asyncio.wait_for(queue.get(), collector.remaining(now))
            except TimeoutError:
                continue
            if msg.is_error_frame or msg.is_remote_frame:
                continue
            if not collector.feed(msg.arbitration_id, bytes(msg.data), time.monotonic()):
                continue
            target = flow_control_to(msg.arbitration_id)
            if target is not None:
                bus.send(
                    can.Message(
                        arbitration_id=target,
                        data=flow_control_frame(stmin, padding),
                        is_extended_id=target > 0x7FF,
                    )
                )
        return collector.result()
    finally:
        notifier.remove_listener(on_frame)
