"""Lossless on-disk folding of repeated captures (``repeat_ms`` / ``repeat_inline_ms``).

A keep-all recording stores every poll, and a stationary signal answers with the
same payload poll after poll, so most rows of a long session differ from an earlier
row only in their ``time``. On disk such a row is **folded** into the previous
stored row for the same ``(rx, pid)``: it becomes one entry of that row's gap list,
the milliseconds since the previous poll of the run. Loading **expands** every fold
back into its own row, so folding is purely an encoding: nothing above
:mod:`canlib.captures.io` ever sees a gap list.

The stored order of a session's rows has to survive, and sessions come in two
shapes, so a run is folded in one of two ways:

- ``repeat_ms`` — the repeats were interleaved with other PIDs' polls (a journaled
  monitor recording). Expansion walks the stored rows in order and merges these
  repeats back in by ``(time, stored position, repeat number)``: before emitting
  stored row ``j`` it emits every pending repeat that sorts before ``j``. Not a
  plain sort by time, because rows are not always in time order (a reconnect, a
  merged journal).
- ``repeat_inline_ms`` — the repeats directly followed the row (a session stored
  grouped by PID). Expansion emits them right after it.

:func:`fold_captures` simulates the expansion and folds a row only when expansion
is guaranteed to put it back where it was, so ``expand_captures(fold_captures(rows))
== rows`` for every input. A round-trip check backs that guarantee up: a session
that would not survive it is stored unfolded.

What may be folded: a row identical to its run's first row in every field and
field order except ``time``, whose time is in the canonical ``HH:MM:SS.mmm`` or
``HH:MM:SS`` form at the same precision as the run's first row. Anything else
(no ``time``, ``elapsed_ms``, a note, a scan) starts a new stored row.
"""

from __future__ import annotations

import bisect
import heapq
import math
import re
from collections.abc import Callable, Mapping, Sequence
from typing import Any

MERGED_FIELD = "repeat_ms"
INLINE_FIELD = "repeat_inline_ms"
REPEAT_FIELDS = (MERGED_FIELD, INLINE_FIELD)

_TIME_RE = re.compile(r"^([01]\d|2[0-3]):([0-5]\d):([0-5]\d)(?:\.(\d{3}))?$")

_Key = tuple[float, int, int]


class CaptureFormatError(ValueError):
    """A capture file's repeat encoding is malformed and cannot be expanded."""


def _parse_ms(time: object) -> tuple[int, bool] | None:
    """``(milliseconds since midnight, has_ms)`` for a canonical time, else None."""
    if not isinstance(time, str):
        return None
    m = _TIME_RE.match(time)
    if m is None:
        return None
    h, mi, s, frac = m.groups()
    ms = ((int(h) * 60 + int(mi)) * 60 + int(s)) * 1000 + (int(frac) if frac else 0)
    return ms, frac is not None


def _format_ms(ms: int, has_ms: bool) -> str | None:
    """The canonical time string for ``ms``, or None when it cannot be written."""
    if not 0 <= ms < 86_400_000 or (not has_ms and ms % 1000):
        return None
    s, frac = divmod(ms, 1000)
    m, s = divmod(s, 60)
    h, m = divmod(m, 60)
    base = f"{h:02d}:{m:02d}:{s:02d}"
    return f"{base}.{frac:03d}" if has_ms else base


def _sort_time(row: Mapping[str, Any]) -> float:
    """The time a stored row sorts at during expansion; untimed rows sort last."""
    parsed = _parse_ms(row.get("time"))
    return math.inf if parsed is None else parsed[0]


def _shape(row: Mapping[str, Any]) -> tuple:
    """Everything a repeat must share with its run's first row: all fields but ``time``."""
    return tuple((k, v) for k, v in row.items() if k != "time")


def _run_key(row: Mapping[str, Any]) -> tuple:
    return (row.get("rx", row.get("ecu")), row.get("pid"))


def _repeats(j: int, row: dict, field: str, gaps: object) -> list[tuple[int, dict]]:
    """The ``(absolute ms, row)`` repeats a stored row's gap list encodes."""
    parsed = _parse_ms(row.get("time"))
    if parsed is None or not isinstance(gaps, list):
        raise CaptureFormatError(
            f"capture {j}: {field} needs a list and a canonical HH:MM:SS[.mmm] time"
        )
    at, has_ms = parsed
    out = []
    for k, gap in enumerate(gaps):
        if not isinstance(gap, int) or isinstance(gap, bool):
            raise CaptureFormatError(f"capture {j}: {field}[{k}] is not an integer")
        at += gap
        time = _format_ms(at, has_ms)
        if time is None:
            raise CaptureFormatError(
                f"capture {j}: {field}[{k}] lands outside the day or the time's precision"
            )
        out.append((at, {**row, "time": time}))
    return out


def expand_captures(stored: Sequence[Any]) -> list:
    """Expand a session's stored captures back to one row per poll.

    Raises :class:`CaptureFormatError` on a malformed gap list.
    """
    if not any(isinstance(c, Mapping) and (c.keys() & REPEAT_FIELDS) for c in stored):
        return list(stored)
    out: list = []
    pending: list[tuple[_Key, dict]] = []
    for j, cap in enumerate(stored):
        if not isinstance(cap, Mapping):
            out.append(cap)
            continue
        key = (_sort_time(cap), j, -1)
        while pending and pending[0][0] < key:
            out.append(heapq.heappop(pending)[1])
        row = dict(cap)
        merged, inline = row.pop(MERGED_FIELD, None), row.pop(INLINE_FIELD, None)
        if merged is not None and inline is not None:
            raise CaptureFormatError(f"capture {j}: both {MERGED_FIELD} and {INLINE_FIELD}")
        out.append(row)
        if inline is not None:
            out.extend(r for _at, r in _repeats(j, row, INLINE_FIELD, inline))
        if merged is not None:
            for k, (at, r) in enumerate(_repeats(j, row, MERGED_FIELD, merged)):
                heapq.heappush(pending, ((at, j, k), r))
    while pending:
        out.append(heapq.heappop(pending)[1])
    return out


class _Folder:
    """One pass of :func:`fold_captures` over a session's rows, in order."""

    def __init__(self) -> None:
        self.stored: list[Mapping[str, Any]] = []
        self.gaps: list[list[int]] = []  # per stored row, its repeat gaps
        self.inline: list[bool] = []  # per stored row, whether its repeats are inline
        self.last_ms: list[int] = []  # per stored row, absolute time of its run's last poll
        self.open: dict[tuple, int] = {}  # (rx, pid) → the stored row a repeat may fold into
        self.tail: int | None = None  # the stored row whose run the previous row ended
        # Merged repeats folded since the last stored row, in order: (key, stored index, row).
        self.pending: list[tuple[_Key, int, Mapping[str, Any]]] = []
        # Stored-row keys as a monotonic stack (index ascending, key descending), so
        # "the largest key of any stored row after h" is one bisect.
        self.stack_idx: list[int] = []
        self.stack_key: list[_Key] = []

    def _max_key_after(self, h: int) -> _Key | None:
        i = bisect.bisect_right(self.stack_idx, h)
        return self.stack_key[i] if i < len(self.stack_idx) else None

    def _try_fold(self, row: Mapping[str, Any]) -> bool:
        h = self.open.get(_run_key(row))
        if h is None:
            return False
        first = self.stored[h]
        parsed, head = _parse_ms(row.get("time")), _parse_ms(first.get("time"))
        if parsed is None or head is None or parsed[1] != head[1]:
            return False
        if _shape(row) != _shape(first):
            return False
        at = parsed[0]
        if not self.gaps[h] and self.tail == h:
            self.inline[h] = True
        if self.inline[h]:
            # Expansion emits inline repeats straight after their row, so only a
            # row directly continuing the run can join it.
            if self.tail != h:
                return False
        else:
            key = (float(at), h, len(self.gaps[h]))
            # Expansion emits merged repeats in key order, and a repeat of h is
            # pending from h onwards, so it must sort after every repeat already
            # pending and after every row stored since h — or it comes out early.
            if self.pending and not self.pending[-1][0] < key:
                return False
            later = self._max_key_after(h)
            if later is not None and not later < key:
                return False
            self.pending.append((key, h, row))
        self.gaps[h].append(at - self.last_ms[h])
        self.last_ms[h] = at
        self.tail = h
        return True

    def _store(self, row: Mapping[str, Any]) -> None:
        key = (_sort_time(row), len(self.stored), -1)
        if self.pending and not self.pending[-1][0] < key:
            # Expansion would emit the pending repeats after this row, so store them
            # as rows of their own instead. They are the tails of their runs.
            unfold, self.pending = self.pending, []
            for _key, h, _row in reversed(unfold):
                self.gaps[h].pop()
            for _key, _h, again in unfold:
                self._store(again)
            key = (key[0], len(self.stored), -1)
        self.pending = []
        idx = len(self.stored)
        self.stored.append(row)
        self.gaps.append([])
        self.inline.append(False)
        parsed = _parse_ms(row.get("time"))
        self.last_ms.append(parsed[0] if parsed else 0)
        self.open[_run_key(row)] = idx
        self.tail = idx
        while self.stack_key and self.stack_key[-1] <= key:
            self.stack_key.pop()
            self.stack_idx.pop()
        self.stack_idx.append(idx)
        self.stack_key.append(key)

    def add(self, row: Mapping[str, Any]) -> None:
        if not self._try_fold(row):
            self._store(row)

    def result(self) -> list:
        out = []
        for row, gaps, inline in zip(self.stored, self.gaps, self.inline, strict=True):
            out.append({**row, (INLINE_FIELD if inline else MERGED_FIELD): gaps} if gaps else row)
        return out


def fold_captures(rows: Sequence[Any]) -> list:
    """Fold a session's captures for storage; the inverse of :func:`expand_captures`.

    Returns the rows unchanged when nothing folds, or when the fold would not
    round-trip (which the folder is built never to produce).
    """
    if not all(isinstance(r, Mapping) for r in rows):
        return list(rows)
    if any(r.keys() & REPEAT_FIELDS for r in rows):
        raise CaptureFormatError("an in-memory capture already carries a repeat gap list")
    folder = _Folder()
    for row in rows:
        folder.add(row)
    folded = folder.result()
    if len(folded) == len(rows) or expand_captures(folded) != list(rows):
        return list(rows)
    return folded


def _map_sessions(doc: Any, fn: Callable[[Sequence[Any]], list]) -> Any:
    if not isinstance(doc, Mapping) or not isinstance(doc.get("sessions"), list):
        return doc
    sessions = []
    for s in doc["sessions"]:
        if isinstance(s, Mapping) and isinstance(s.get("captures"), list):
            s = {**s, "captures": fn(s["captures"])}
        sessions.append(s)
    return {**doc, "sessions": sessions}


def fold_file(doc: Any) -> Any:
    """A copy of a capture document with every session's captures folded."""
    return _map_sessions(doc, fold_captures)


def expand_file(doc: Any) -> Any:
    """A capture document with every session's captures expanded to one row per poll."""
    return _map_sessions(doc, expand_captures)
