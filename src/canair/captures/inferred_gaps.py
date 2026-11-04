"""Gaps a session's timestamps imply but its ``link_events`` do not record.

Sessions recorded before link events existed (and any recorder that keeps no events)
still show their holes in the timestamps: a stretch in which *no* PID stored a row. In a
session that keeps every poll that means nothing was gathered, which is worth pointing
out. It is a guess, so it is only ever computed for display, never stored beside the
recorded events, and a recorded session-wide gap that overlaps it explains it away.

A run-length (``keep:changes``, legacy ``keep:unique``) session is skipped: an absent row
there means the value did not change, so a hole proves nothing.

A **pure leaf** over capture times, like :mod:`canair.captures.link_events`.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from itertools import pairwise
from statistics import median
from typing import Any

from canair.captures.link_events import SESSION_GAP_KINDS, gap_intervals
from canair.states.spans import parse_time_key

# A hole is inferred when it is longer than both of these: an absolute floor (a
# keep-all session polls every few seconds at most), and a multiple of the session's
# own typical spacing (so one slow PID polled once a minute is not a gap every minute).
MIN_GAP_S = 30.0
TYPICAL_FACTOR = 10.0
# Too few rows to say what the spacing is.
MIN_ROWS = 10

RUN_LENGTH_KEEP_MODES = frozenset({"changes", "unique"})


def infer_gaps(
    times: Iterable[Any],
    events: Sequence[Mapping[str, Any]] = (),
    keep_mode: str | None = "",
) -> list[tuple[float, float]]:
    """``(start, end)`` seconds since midnight of each unexplained hole in ``times``.

    ``times`` are the session's capture times (``HH:MM:SS[.fff]``), in any order;
    ``events`` its recorded link events, whose session-wide gaps explain a hole they
    overlap. A session crossing midnight is out of scope, as it is for time joins.
    """
    if str(keep_mode or "") in RUN_LENGTH_KEEP_MODES:
        return []
    keys = sorted({k for k in (parse_time_key(t) for t in times) if k is not None})
    if len(keys) < MIN_ROWS:
        return []
    deltas = [b - a for a, b in pairwise(keys)]
    threshold = max(MIN_GAP_S, TYPICAL_FACTOR * median(deltas))
    recorded = gap_intervals(events, kinds=SESSION_GAP_KINDS)
    return [
        (a, b)
        for a, b in pairwise(keys)
        if b - a > threshold
        and not any(start < b and (end is None or end > a) for start, end in recorded)
    ]


def format_key(key: float) -> str:
    """Seconds since midnight as ``HH:MM:SS``."""
    s = int(key)
    return f"{s // 3600:02d}:{s % 3600 // 60:02d}:{s % 60:02d}"
