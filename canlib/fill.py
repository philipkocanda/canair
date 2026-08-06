#!/usr/bin/env python3
"""Validity intervals for run-length (``keep:changes``) capture data.

``canair monitor --save`` defaults to ``--keep-changes``: a payload is stored only
when it *differs from the immediately preceding one* for that PID. That is the
right storage decision — a body ECU polled for three hours while the car sits
parked would otherwise write thousands of identical rows — but it means a stored
row is a **run-length segment**, not a sample. Its value is known to hold until
the next stored row for that PID.

The time-aligned joins (``align``/``correlate``/``hunt``/``investigate``) treated
every stored row as a point sample, so a signal that legitimately did not change
had nothing to attach to and the reference row was silently dropped. Measured on
the bundled profile: aligning IGPM ``CHARGE_PORT_LOCK`` (known with certainty for
a whole 3 h charge, in which it changed exactly twice) against BMS ``SOC_BMS``
joined 5 of 2016 rows — a 99.75 % loss of a window that was never unknown. See
``plans/2026-08-05-run-length-forward-fill-joins.md``.

This module owns the fix, and deliberately owns *only* the policy half of it: it
turns capture rows into one derived quantity per row — **``hold_until``**, the
instant a stored value stops being known — which the join primitives then consult
as a fallback. Everything that needs provenance (the session's ``keep_mode``, the
session identity, the session's end) is decided here, once, while the capture rows
are still adjacent; :mod:`canlib.align` stays pure mechanism and never learns what
a keep mode is.

Pure inspection over already-loaded capture entries: no I/O, and no canair imports
beyond the capture row helpers, so any layer may depend on it.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Literal, get_args

from .capture_dates import entry_datetime
from .capture_types import CaptureEntry
from .keepmode import KEEP_CHANGES, KEEP_UNIQUE, scope_is_keep_unique

__all__ = [
    "FILL_AUTO",
    "FILL_HOLD",
    "FILL_MODES",
    "FILL_NONE",
    "FORCED_HOLD_WARNING",
    "FillMode",
    "FillPolicy",
    "SessionKey",
    "forced_hold_warning",
    "format_hold_duration",
    "hold_until_vector",
    "parse_fill_mode",
    "session_end_times",
    "session_key",
]

# How a run-length value may be carried forward onto a reference instant it has no
# stored sample at.
#
# ``auto`` is the default because dropping the row is a data-loss bug rather than a
# conservative choice, but it fills only what it can defend: a row whose session
# recorded ``keep_mode: changes``, where the *absence* of a later row is itself the
# evidence that the value did not change. ``hold`` is the escape hatch for data
# whose provenance is unrecorded (a legacy session, or ``keep:all``, both of which
# store no keep mode) — the user asserting run-length semantics the file can't
# prove. ``none`` restores strict point semantics, for comparing against a
# pre-fill result.
FillMode = Literal["auto", "hold", "none"]
FILL_MODES: tuple[FillMode, ...] = get_args(FillMode)

FILL_AUTO: FillMode = "auto"
FILL_HOLD: FillMode = "hold"
FILL_NONE: FillMode = "none"

# ``(file, session index)`` — the canonical session identity, same key
# ``capture_dates``/``captures`` use. A value is never carried across it: the ECU
# may have changed unobserved between two recordings.
SessionKey = tuple[str, int]


def parse_fill_mode(value: object) -> FillMode:
    """Narrow an untrusted value to a :data:`FillMode`, defaulting to ``auto``."""
    for mode in FILL_MODES:
        if value == mode:
            return mode
    return FILL_AUTO


@dataclass(frozen=True)
class FillPolicy:
    """Whether and how far a stored value may be carried forward.

    Frozen (and therefore hashable) so a :class:`~canlib.align.LoadedPid` can
    memoise its hold vector per policy — ``investigate --bits`` builds hundreds of
    series over one PID and must not recompute it per series.
    """

    mode: FillMode = FILL_AUTO
    max_hold_s: float | None = None

    @property
    def enabled(self) -> bool:
        return self.mode != FILL_NONE

    def allows(self, entry: CaptureEntry) -> bool:
        """True when ``entry``'s stored value may be held past its own timestamp.

        Under ``auto`` this is a *per-row* test, not a per-scope one: a scope
        spanning a run-length session and a ``keep:unique`` one fills only the rows
        it may, instead of the whole scope degrading to the weakest provenance.
        """
        if self.mode == FILL_NONE:
            return False
        if self.mode == FILL_HOLD:
            return True
        return str(entry.get("keep_mode") or "") == KEEP_CHANGES


def session_key(entry: CaptureEntry) -> SessionKey:
    """``entry``'s owning session identity."""
    return (str(entry.get("file", "")), int(entry.get("_session_idx", -1)))


def session_end_times(entries: Iterable[CaptureEntry]) -> dict[SessionKey, datetime]:
    """The last timestamp seen in each session, across *every* PID it recorded.

    The closing bound for a session's final run: the value is known to have held
    at least as long as the recording continued, and no longer. Deliberately
    computed over all of a session's captures rather than one PID's — a PID whose
    own last capture is early in the session was still being polled after it (the
    absence of a later row is what proves the value held), so its own last
    timestamp would close the run far too early.
    """
    ends: dict[SessionKey, datetime] = {}
    for e in entries:
        dt = entry_datetime(e)
        if dt is None:
            continue
        key = session_key(e)
        prev = ends.get(key)
        if prev is None or dt > prev:
            ends[key] = dt
    return ends


def hold_until_vector(
    entries: Sequence[CaptureEntry],
    dts: Sequence[datetime],
    *,
    session_ends: dict[SessionKey, datetime],
    policy: FillPolicy,
) -> list[datetime | None]:
    """When each row's value stops being known, aligned with ``entries``/``dts``.

    ``entries`` and ``dts`` are parallel (the caller has already decoded and
    dropped untimed rows, so the timestamps are non-optional and need not be
    re-derived). Returns a same-length list where ``None`` means "point sample" —
    not eligible for filling, or with nothing to fill into.

    The window is ``min(next capture of this PID in the same session, that
    session's end, this row's timestamp + max_hold_s)``. Rows are visited in time
    order to find each one's successor, but the result is returned in **input
    order** so a caller can zip it against an unsorted frame list.
    """
    n = len(entries)
    holds: list[datetime | None] = [None] * n
    if not policy.enabled or n == 0:
        return holds

    # Close each row at the next row of the *same session*: walking in time order,
    # the row currently open for a session is closed by the next one to arrive.
    open_row: dict[SessionKey, int] = {}
    for i in sorted(range(n), key=lambda i: dts[i]):
        key = session_key(entries[i])
        prev = open_row.get(key)
        if prev is not None:
            holds[prev] = dts[i]
        open_row[key] = i
    for key, i in open_row.items():
        holds[i] = session_ends.get(key)

    cap = timedelta(seconds=policy.max_hold_s) if policy.max_hold_s else None
    for i in range(n):
        if not policy.allows(entries[i]):
            holds[i] = None
            continue
        until = holds[i]
        if cap is not None:
            capped = dts[i] + cap
            if until is None or capped < until:
                until = capped
        # A run with no width carries nothing (the session ended on this row).
        holds[i] = until if until is not None and until > dts[i] else None
    return holds


def format_hold_duration(seconds: float) -> str:
    """A carry length a reader can judge at a glance (``12s`` / ``4m`` / ``2h58m``).

    How *far* a value was held is what separates a defensible fill from a
    suspicious one, so every command that reports a fill count reports this too —
    from one shared spelling, so the units can't differ between reports.
    """
    total = round(seconds)
    if total < 60:
        return f"{total}s"
    if total < 3600:
        return f"{total // 60}m"
    return f"{total // 3600}h{(total % 3600) // 60:02d}m"


# Raised when ``--fill hold`` is forced onto ``keep:unique`` data.
#
# ``keep:unique`` dedup is *global*, so a return to any previously-seen value was
# never stored: the run structure is genuinely lost and a stored row's value is not
# known to have held until the next one. ``auto`` therefore never fills such a row;
# ``--fill hold`` overrides that, which is the user's call to make — but loudly,
# since the reconstructed segments may be fiction.
FORCED_HOLD_WARNING = (
    f"warning: --fill hold is carrying values forward across keep:{KEEP_UNIQUE} "
    "sessions — global dedup discarded return-to-previous transitions, so a stored "
    "row's value is not known to have held until the next one. The filled segments "
    "may be fiction; prefer --fill auto (or re-record with --keep-changes)."
)


def forced_hold_warning(entries: Iterable[CaptureEntry], policy: FillPolicy) -> str | None:
    """:data:`FORCED_HOLD_WARNING` when ``policy`` forces a hold over unfillable data.

    For a caller that already knows its scope's keep modes, compare against
    :data:`FILL_HOLD` and use the constant directly.
    """
    if policy.mode != FILL_HOLD or not scope_is_keep_unique(entries):
        return None
    return FORCED_HOLD_WARNING
