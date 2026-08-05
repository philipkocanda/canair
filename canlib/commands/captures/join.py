#!/usr/bin/env python3
"""N-way time join for the stacked capture-compare view.

Turns several PIDs' captures into a shared timeline of frames, each frame
holding one slot per selected (ECU, PID) key. Pure data — the rendering lives in
:mod:`step_render`, the state in :mod:`step_model`.

The nearest-within-tolerance rule deliberately mirrors
:func:`canlib.align.join_prepared`, so the stepper pairs captures exactly as
``align``/``correlate``/``hunt`` do (``tests/test_captures.py`` asserts the
parity).
"""

from __future__ import annotations

from bisect import bisect_left
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime

from canlib.capture_dates import entry_datetime
from canlib.capture_types import CaptureEntry

from .query import _capture_key


@dataclass(frozen=True)
class JoinFrame:
    """One row of the stacked compare view: an anchor time + one slot per key.

    ``indices`` is parallel to the caller's key order; a slot is ``None`` when
    that key had no capture within the join tolerance of ``anchor_dt``.
    """

    anchor_dt: datetime
    indices: tuple[int | None, ...]


def _nearest_within(ts: list[float], t: float, tol_s: float) -> int | None:
    """Position in the sorted ``ts`` nearest to ``t``, or None if none within ``tol_s``.

    Mirrors the nearest-within-tolerance rule of
    :func:`canlib.align.join_prepared`: the smaller absolute delta wins, and an
    exact tie resolves to the *earlier* sample.
    """
    if not ts:
        return None
    pos = bisect_left(ts, t)
    best: int | None = None
    best_d = float("inf")
    # The nearest sample is one of the two straddling `t`; check the earlier
    # candidate first so an exact tie keeps it (align's tie rule).
    for cand in (pos - 1, pos):
        if 0 <= cand < len(ts):
            d = abs(ts[cand] - t)
            if d < best_d:
                best, best_d = cand, d
    if best is None or best_d > tol_s:
        return None
    return best


def build_join_frames(
    captures: Sequence[CaptureEntry],
    keys: list[tuple[str, str]],
    tol_s: float,
) -> tuple[list[JoinFrame], int]:
    """Join captures of several (ECU, PID) keys into a stacked, time-aligned timeline.

    Every *timed* capture anchors a frame (the union of all timestamps), and each
    key contributes the capture nearest that anchor within ``tol_s`` seconds —
    so nothing is hidden: a capture with no counterpart still appears, alone.
    Consecutive frames resolving to the *same* set of captures collapse into one
    (a round-robin poll of N PIDs within the tolerance yields N identical
    anchors); the collapse is deliberately consecutive-only, so a set that
    recurs later in the timeline still gets its own frame.

    ``keys`` fixes the slot order of :attr:`JoinFrame.indices` (the on-screen
    block order); a key with no captures contributes an always-``None`` slot.
    Returns ``(frames, n_no_time)``, the latter counting captures excluded for
    lacking a usable timestamp (as in :mod:`canlib.align`).
    """
    per_key: dict[tuple[str, str], list[int]] = {k: [] for k in keys}
    dts: dict[int, datetime] = {}
    n_no_time = 0
    for idx, e in enumerate(captures):
        bucket = per_key.get(_capture_key(e))
        if bucket is None:
            continue
        dt = entry_datetime(e)
        if dt is None:
            n_no_time += 1
            continue
        dts[idx] = dt
        bucket.append(idx)

    for bucket in per_key.values():
        bucket.sort(key=lambda i: dts[i])
    # Epoch floats per key for the bisect join (parallel to per_key's lists).
    epochs = {k: [dts[i].timestamp() for i in idxs] for k, idxs in per_key.items()}

    # Union of every timestamp, ordered by time then key order for a stable
    # anchor sequence when two keys share an instant.
    key_order = {k: n for n, k in enumerate(keys)}
    anchors = sorted(dts, key=lambda i: (dts[i], key_order.get(_capture_key(captures[i]), 0)))

    frames: list[JoinFrame] = []
    prev: tuple[int | None, ...] | None = None
    for a in anchors:
        t = dts[a].timestamp()
        row = tuple(
            per_key[k][pos] if (pos := _nearest_within(epochs[k], t, tol_s)) is not None else None
            for k in keys
        )
        if row == prev:
            continue
        frames.append(JoinFrame(anchor_dt=dts[a], indices=row))
        prev = row
    return frames, n_no_time
