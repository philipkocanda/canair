"""Shortening the ELM327's own ECU wait while a sleeping ECU is woken.

A wake ritual fires requests back-to-back so a fast-sleeping ECU (the Ioniq's SKM, ~2 s
sleep timer) never sees a quiet gap. On an ELM327 the gap is set by the adapter, not by
canair: after each request it waits its ``ATST`` budget (about 614 ms at ``ATST96``) to be
sure no further frame is coming, and takes no new command until it has. ``ATST10`` (64 ms)
for the length of the wake is what makes the burst dense.

The ritual itself is :class:`canair.transport.session_manager.SessionManager`'s; this module
owns only the adapter setting. The setting is restored on the way out (an adapter setting
written outside the init string must not outlive the session), to the wait the session
intends, ``Elm327Terminal.elm_timeout_cmd``, which the engine also restores after the adapter
resets itself.
"""

from __future__ import annotations

import contextlib
from collections.abc import AsyncIterator
from typing import TYPE_CHECKING

if TYPE_CHECKING:  # pragma: no cover - typing only
    from canair.transport.elm327.terminal import Elm327Terminal

#: The adapter wait during a wake: 0x10 units of 4 ms, 64 ms.
WAKE_TIMEOUT_CMD = "ATST10"


@contextlib.asynccontextmanager
async def short_ecu_wait(terminal: Elm327Terminal) -> AsyncIterator[None]:
    """Hold the terminal with the adapter's ECU wait at :data:`WAKE_TIMEOUT_CMD`.

    One transaction for the whole block, so no other request (a keepalive) runs with the
    short wait and reads ``NO DATA`` from an ECU that is merely slow.
    """
    async with terminal.transaction():
        await terminal.send_command(WAKE_TIMEOUT_CMD)
        try:
            yield
        finally:
            await terminal.send_command(terminal.elm_timeout_cmd)
