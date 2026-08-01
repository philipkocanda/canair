"""Cross-device connect-time fallback selection.

When the user has several configured devices (see
:func:`canlib.config.wican_devices`) and the selected one is unreachable, try
the others rather than failing outright. :func:`resolve_transport_candidates`
produces the ordered candidate list; this module picks the first one that
answers a fast liveness probe, using a short, configurable connect timeout so a
dead device is skipped quickly.

Only a liveness probe lives here — the chosen transport still goes through the
normal ``require_*_reachable`` + connect path (with its rich error) downstream,
so the "all candidates down" case reports exactly as it does today.
"""

from __future__ import annotations

import sys
from collections.abc import Callable

from .config import TransportConfig


def _probe_port(cand: TransportConfig) -> int:
    """Port to probe for device liveness.

    Both current transports reach a WiCAN, whose HTTP/config API on port 80 is a
    cheap, mode-independent liveness signal (the ws terminal lives there, and the
    slcan data port's reachability is re-checked on the chosen device downstream).
    A future non-WiCAN gateway (no HTTP) falls back to its data port.
    """
    if cand.is_wican_http:
        return 80
    return cand.port or 3333


def select_reachable_transport(
    candidates: list[TransportConfig],
    *,
    connect_timeout: float,
    notice: Callable[[str], None] | None = None,
) -> TransportConfig:
    """Return the first candidate that answers a liveness probe (primary first).

    With a single candidate (fallback disabled or only one device), returns it
    immediately without probing — preserving today's behaviour. When every
    candidate fails the probe, returns ``candidates[0]`` so the normal
    connect/error path reports the primary's failure as usual.
    """
    if notice is None:

        def notice(msg: str) -> None:
            print(msg, file=sys.stderr)

    if len(candidates) <= 1:
        return candidates[0]

    from ..wican_mode import _tcp_open

    for i, cand in enumerate(candidates):
        if not cand.host:
            continue
        if _tcp_open(cand.host, _probe_port(cand), connect_timeout):
            if i > 0:
                notice(f"note: falling back to {cand.describe()} (earlier device unreachable)")
            return cand
        if i < len(candidates) - 1:
            nxt = candidates[i + 1]
            notice(f"note: {cand.describe()} unreachable — trying {nxt.describe()}…")
    # None reachable: hand back the primary so the normal rich-error path fires.
    return candidates[0]
