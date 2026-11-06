"""Hold a diagnostic session on one ECU for the length of a live operation.

The single-ECU commands (``identity``, ``scan``, ``raw``, ``io``, ``routines``, ``dtc``,
``memory``) open a session when asked, keep it alive while they work and let it time out
afterwards. :class:`~canair.transport.session_manager.SessionManager` does all of that; this
adds what only a live operation knows, the profile, from which a ``--wake`` takes the ECU's
own ``wake:`` ritual (``ecus/wake.py``).
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Mapping
from contextlib import asynccontextmanager
from typing import Any

from canair.ecus.pids import load_pids
from canair.ecus.wake import WakePlan, wake_plan_for
from canair.profile.model import Profile
from canair.transport.protocol import Terminal
from canair.transport.session_manager import DEFAULT_SESSION_MODE, SessionManager, SessionOutcome


@asynccontextmanager
async def held_session(
    terminal: Terminal,
    tx_id: int,
    *,
    profile: Profile,
    enabled: bool = True,
    mode: int | str = DEFAULT_SESSION_MODE,
    wake: bool = False,
    pids_data: Mapping[str, Any] | None = None,
    verbose: bool = False,
) -> AsyncIterator[SessionOutcome | None]:
    """Hold session ``mode`` on ``tx_id`` for the block; yields None when not ``enabled``.

    ``wake`` rouses the ECU first with its ``wake:`` ritual, found in ``pids_data`` (the
    profile's definitions unless given), else with ``10 01``.
    """
    if not enabled:
        yield None
        return
    plan = wake_plan(tx_id, profile=profile, wake=wake, pids_data=pids_data)
    sm = SessionManager(terminal, verbose=verbose)
    async with sm.held(tx_id, mode=mode, wake=wake, wake_plan=plan) as outcome:
        yield outcome


def wake_plan(
    tx_id: int,
    *,
    profile: Profile,
    wake: bool = True,
    pids_data: Mapping[str, Any] | None = None,
) -> WakePlan | None:
    """The ritual a ``--wake`` of ``tx_id`` uses, or None (no wake, or a plain ``10 01``)."""
    if not wake:
        return None
    return wake_plan_for(tx_id, pids_data if pids_data is not None else load_pids(profile))
