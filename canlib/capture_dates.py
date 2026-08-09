#!/usr/bin/env python3
"""Shared date/state/label scoping helpers for capture-consuming commands.

Both ``canair captures`` and ``canair decode`` load capture entries and let the
user narrow them by session date (``--since``/``--until``/``--date``) and by a
session ``state`` (matched by token, widened by ``implies:``) or a substring
of its ``label``. The parsing/filtering logic is
identical, so it lives here and is imported by both to keep their scoping surface
consistent.

Entries are plain dicts; the helpers only read ``date``, ``state``, ``label``
and (for captures) ``session_label`` keys, so any capture-shaped dict works.
"""

import argparse
import sys
from collections.abc import Mapping, Sequence
from datetime import date, datetime, time
from pathlib import Path
from typing import Any

from .capture_store import load_all_captures

# NOTE on the scope filters below: they are deliberately shape-agnostic. Each
# reads only a handful of keys (date/time/vehicle_states/label) and is applied
# both to full `CaptureEntry` rows and to the slimmer row
# `capture_store.load_pid_captures` reshapes to. The `_Row` type parameter keeps
# them honest *and* preserves the caller's row type through the filter, so a
# precisely-typed input doesn't degrade to `list[dict]` on the way out.

__all__ = [
    "active_scope_flags",
    "add_scope_args",
    "entry_date",
    "entry_datetime",
    "filter_by_date_range",
    "filter_by_text",
    "parse_iso_date",
    "parse_iso_datetime",
    "resolve_date_bounds",
    "resolve_scope_bounds",
]

# Accepted time-of-day precisions for --since/--until, most-precise first. The
# date-with-space form and the ISO ``T``-separated form are both accepted.
_DATETIME_FORMATS = (
    "%Y-%m-%d %H:%M:%S.%f",
    "%Y-%m-%d %H:%M:%S",
    "%Y-%m-%d %H:%M",
)


def parse_iso_date(s: str) -> date:
    """Parse an ``YYYY-MM-DD`` string into a ``date`` (for argparse ``type=``).

    Raises ``argparse.ArgumentTypeError`` on a malformed value so argparse emits
    a clean usage error.
    """
    try:
        return datetime.strptime(s.strip(), "%Y-%m-%d").date()
    except ValueError:
        raise argparse.ArgumentTypeError(f"invalid date {s!r} (expected YYYY-MM-DD)") from None


def parse_iso_datetime(s: str) -> date | datetime:
    """Parse ``YYYY-MM-DD`` **or** ``YYYY-MM-DD[ T]HH:MM[:SS[.ffffff]]``.

    Returns a bare ``date`` when no time-of-day is given (so the value keeps its
    whole-day, backward-compatible meaning) and a ``datetime`` when a time is
    present (so ``--since``/``--until`` can narrow to a sub-day instant, down to
    microseconds). For argparse ``type=``; raises ``ArgumentTypeError`` on a
    malformed value.
    """
    raw = s.strip()
    for fmt in _DATETIME_FORMATS:
        for f in (fmt, fmt.replace(" ", "T")):
            try:
                return datetime.strptime(raw, f)
            except ValueError:
                continue
    try:
        return datetime.strptime(raw, "%Y-%m-%d").date()
    except ValueError:
        raise argparse.ArgumentTypeError(
            f"invalid date/time {s!r} (expected YYYY-MM-DD[ HH:MM[:SS[.ffffff]]])"
        ) from None


def _as_lower_datetime(x: date | datetime) -> datetime:
    """Normalize a bound to its earliest instant (date -> start of day)."""
    if isinstance(x, datetime):
        return x
    return datetime.combine(x, time.min)


def _as_upper_datetime(x: date | datetime) -> datetime:
    """Normalize a bound to its latest instant (date -> end of day, inclusive)."""
    if isinstance(x, datetime):
        return x
    return datetime.combine(x, time.max)


def entry_date(entry: Mapping[str, Any]) -> date | None:
    """Parse a capture entry's session ``date`` field, or None if absent/invalid.

    Tolerates a trailing suffix on same-day sessions (e.g. ``2026-04-17-b``) by
    falling back to the leading ``YYYY-MM-DD`` portion, so those captures still
    sort into the correct day when a date filter is active.
    """
    raw = str(entry.get("date", "")).strip()
    if not raw:
        return None
    return _parse_date_cached(raw)


# Session dates repeat across every capture in a file; time-aligned analysis
# parses them millions of times, so memoize the (small, bounded) date strings.
# strptime is comparatively slow (it recompiles format regexes internally), so a
# direct fast-path parse plus this cache removes it from the hot loop.
_date_cache: dict[str, date | None] = {}


def _parse_date_cached(raw: str) -> date | None:
    if raw in _date_cache:
        return _date_cache[raw]
    result = _parse_date(raw)
    _date_cache[raw] = result
    return result


def _parse_date(raw: str) -> date | None:
    """Fast ``YYYY-MM-DD`` parse (tolerating a trailing suffix), strptime fallback."""
    head = raw[:10]
    if len(head) == 10 and head[4] == "-" and head[7] == "-":
        y, m, d = head[:4], head[5:7], head[8:10]
        if y.isdigit() and m.isdigit() and d.isdigit():
            try:
                return date(int(y), int(m), int(d))
            except ValueError:
                return None
    for candidate in (raw, raw[:10]):
        try:
            return datetime.strptime(candidate, "%Y-%m-%d").date()
        except ValueError:
            continue
    return None


def entry_datetime(entry: Mapping[str, Any]) -> datetime | None:
    """Combine a capture entry's session ``date`` + per-capture ``time`` into a
    ``datetime``, or ``None`` if either is absent/unparseable.

    This is the join key for time-aligned cross-signal analysis (``canair
    correlate``/``hunt`` and cross-ECU ``--corr``). Captures with no usable
    ``time`` — one-shot scan/probe/identity reads where a timestamp was never
    meaningful — return ``None`` and are dropped from time joins (but retained
    for value/state analysis). Accepts ``HH:MM:SS`` and ``HH:MM:SS.fff``.
    """
    d = entry_date(entry)
    if d is None:
        return None
    raw = str(entry.get("time", "")).strip()
    if not raw:
        return None
    t = _parse_time(raw)
    if t is None:
        return None
    return datetime.combine(d, t)


def _parse_time(raw: str) -> time | None:
    """Fast ``HH:MM:SS[.ffffff]`` / ``HH:MM`` parse, strptime fallback.

    strptime dominates time-aligned analysis (called once per capture per build
    pass, i.e. millions of times); the fixed capture formats parse directly with
    no regex, and only exotic inputs fall through to strptime.
    """
    parts = raw.split(":")
    try:
        if len(parts) == 3:
            hh, mm = int(parts[0]), int(parts[1])
            sec = parts[2]
            if "." in sec:
                whole, frac = sec.split(".", 1)
                ss = int(whole)
                # Right-pad/truncate the fraction to microseconds (6 digits).
                micro = int((frac + "000000")[:6])
            else:
                ss, micro = int(sec), 0
            return time(hh, mm, ss, micro)
        if len(parts) == 2:
            return time(int(parts[0]), int(parts[1]))
    except (ValueError, IndexError):
        pass
    for fmt in ("%H:%M:%S.%f", "%H:%M:%S", "%H:%M"):
        try:
            return datetime.strptime(raw, fmt).time()
        except ValueError:
            continue
    return None


def filter_by_date_range[Row: Mapping[str, Any]](
    entries: list[Row],
    since: date | datetime | None = None,
    until: date | datetime | None = None,
) -> list[Row]:
    """Keep entries whose session date/time falls within ``[since, until]`` (inclusive).

    Either bound may be ``None`` (open-ended). A bound may be a ``date`` (whole
    day) or a ``datetime`` (a sub-day instant, down to microseconds).

    When **neither** bound carries a time-of-day, comparison is date-only:
    entries without a parseable date are dropped, but untimed captures within
    the day range (one-shot scan/identity reads) are kept — the original
    behavior. When **either** bound carries a time-of-day, comparison is
    time-aware against each capture's ``date``+``time``: captures with no usable
    timestamp cannot be placed on the timeline and are dropped.
    """
    if since is None and until is None:
        return entries

    time_aware = isinstance(since, datetime) or isinstance(until, datetime)
    if not time_aware:
        out = []
        for e in entries:
            d = entry_date(e)
            if d is None:
                continue
            if since is not None and d < since:
                continue
            if until is not None and d > until:
                continue
            out.append(e)
        return out

    lo = _as_lower_datetime(since) if since is not None else None
    hi = _as_upper_datetime(until) if until is not None else None
    out = []
    for e in entries:
        dt = entry_datetime(e)
        if dt is None:
            continue
        if lo is not None and dt < lo:
            continue
        if hi is not None and dt > hi:
            continue
        out.append(e)
    return out


def filter_by_text[Row: Mapping[str, Any]](
    entries: list[Row],
    state: str | Sequence[str] | None = None,
    label: str | None = None,
) -> list[Row]:
    """Keep entries whose ``vehicle_states``/``label`` match the selector.

    ``state`` is matched by *token* against the capture's resolved
    ``vehicle_states``, expanded through the profile's ``implies:`` hierarchy —
    see :func:`states.state_matcher` for the alternative/conjunctive grammar. It
    reads the per-capture value that ``capture_store`` resolved from the
    session's ``state_spans``, so a session that charged and then drove no longer
    returns its driving captures under ``--state CHARGING``.

    ``label`` stays a case-insensitive substring, matched against both the
    session label (stored as ``session_label`` by ``captures`` and ``label`` by
    ``decode``) and any per-capture ``label``. Both filters are ANDed; ``None``
    means "don't filter on this field".
    """
    from .states import state_matcher

    if not state and not label:
        return entries
    matches_state = state_matcher(state) if state else None
    l_needle = label.lower() if label else None
    out = []
    for e in entries:
        if matches_state is not None and not matches_state(e.get("vehicle_states")):
            continue
        if l_needle is not None:
            haystack = f"{e.get('session_label', '')} {e.get('label', '')}".lower()
            if l_needle not in haystack:
                continue
        out.append(e)
    return out


def active_scope_flags(args: argparse.Namespace) -> list[str]:
    """Names of the scope flags actually set on ``args``.

    The user-facing spelling of every scoping flag that is set (empty when the
    invocation is unscoped), for a command that needs to warn when a scope was
    applied — e.g. ``investigate --counters``, which wants the whole history.
    Checks the raw flags rather than a resolved ``since``/``until`` so
    ``--last-session`` (which resolves *into* a ``since`` cutoff) is still named.
    """
    out: list[str] = []
    if getattr(args, "since", None) is not None:
        out.append("--since")
    if getattr(args, "until", None) is not None:
        out.append("--until")
    if getattr(args, "date", None) is not None:
        out.append("--date")
    if getattr(args, "today", False):
        out.append("--today")
    if getattr(args, "last_sessions", None):
        out.append("--last-session")
    if getattr(args, "state", None) is not None:
        out.append("--state")
    if getattr(args, "label", None) is not None:
        out.append("--label")
    return out


def add_scope_args(parser: argparse.ArgumentParser) -> None:
    """Add the shared ``--since/--until/--date`` and ``--state/--label`` scoping flags.

    Kept in one place so ``captures`` and ``decode`` expose an identical scoping
    surface (same flag names, metavars, and help text).
    """
    date_group = parser.add_argument_group(
        "scoping",
        "Restrict to captures within a date/time range (inclusive) and/or by "
        "session state (token-matched) or label substring. --since/--until accept a date "
        "(YYYY-MM-DD) or a timestamp (YYYY-MM-DD HH:MM[:SS[.ffffff]])",
    )
    date_group.add_argument(
        "--since",
        type=parse_iso_datetime,
        metavar="WHEN",
        help="Only captures on or after this date/time (YYYY-MM-DD[ HH:MM:SS])",
    )
    date_group.add_argument(
        "--until",
        type=parse_iso_datetime,
        metavar="WHEN",
        help="Only captures on or before this date/time (YYYY-MM-DD[ HH:MM:SS])",
    )
    date_group.add_argument(
        "--date",
        type=parse_iso_date,
        metavar="YYYY-MM-DD",
        help="Only captures on this exact date (shorthand for --since X --until X)",
    )
    date_group.add_argument(
        "--today",
        action="store_true",
        help="Only captures recorded today (shorthand for --date <today>)",
    )
    date_group.add_argument(
        "--last-sessions",
        nargs="?",
        type=int,
        const=1,
        default=None,
        metavar="N",
        dest="last_sessions",
        help="Only the most recent N recorded sessions in scope (N defaults to 1)",
    )
    date_group.add_argument(
        "--last-session",
        action="store_const",
        const=1,
        dest="last_sessions",
        help="Only the most recent recorded session in scope (alias for --last-sessions 1)",
    )
    date_group.add_argument(
        "--state",
        action="append",
        metavar="STATE",
        help="Only captures recorded in STATE, matched by token and widened by the "
        "profile's implies: hierarchy (--state ready also matches DRIVING). "
        "Comma-separate alternatives (--state ready,driving); repeat the flag to "
        "require several at once (--state charging --state parked)",
    )
    date_group.add_argument(
        "--label",
        metavar="SUBSTR",
        help="Only captures whose session/capture label contains SUBSTR (case-insensitive)",
    )


def resolve_date_bounds(
    args: argparse.Namespace,
) -> tuple[date | datetime | None, date | datetime | None, str | None]:
    """Resolve ``--date``/``--since``/``--until``/``--today`` into ``(since, until, error)``.

    ``--date`` is shorthand for an equal since/until pair (a whole day) and
    ``--today`` for ``--date <today>``; both are mutually exclusive with
    ``--since``/``--until``. ``--since``/``--until`` may each be a ``date`` or a
    ``datetime`` (see ``parse_iso_datetime``). Returns an error message string
    (for the caller to print and exit non-zero) instead of raising, or ``None``
    on success.
    """
    date_ = getattr(args, "date", None)
    since = getattr(args, "since", None)
    until = getattr(args, "until", None)
    today = getattr(args, "today", False)
    if today:
        if date_ or since or until:
            return None, None, "--today cannot be combined with --since/--until/--date"
        date_ = date.today()
    if date_ and (since or until):
        return None, None, "--date cannot be combined with --since/--until"
    since = date_ or since
    until = date_ or until
    if since and until and _as_lower_datetime(since) > _as_upper_datetime(until):
        return None, None, f"--since ({since}) is after --until ({until})"
    return since, until, None


def _session_starts(
    since: date | datetime | None,
    until: date | datetime | None,
    state: str | Sequence[str] | None,
    label: str | None,
    captures_dir: Path | None = None,
) -> list[datetime]:
    """Start instant of each recorded session in scope, chronological.

    A session's start is the earliest capture timestamp in it (falling back to
    start-of-day for a session with only untimed captures). Sessions with no
    placeable timestamp are skipped, so they can't anchor a --last-sessions
    window (consistent with time-aware date filtering dropping untimed data).
    """
    entries = load_all_captures(captures_dir)
    entries = filter_by_date_range(entries, since, until)
    entries = filter_by_text(entries, state=state, label=label)

    starts: dict[tuple[str, int], datetime] = {}
    for e in entries:
        key = (e.get("file", ""), e.get("_session_idx", 0))
        dt = entry_datetime(e)
        if dt is None:
            d = entry_date(e)
            if d is None:
                continue
            dt = _as_lower_datetime(d)
        if key not in starts or dt < starts[key]:
            starts[key] = dt
    return sorted(starts.values())


def resolve_scope_bounds(
    args: argparse.Namespace, captures_dir: Path | None = None
) -> tuple[date | datetime | None, date | datetime | None, str | None]:
    """Resolve the full scope surface into ``(since, until, error)``.

    Extends :func:`resolve_date_bounds` (``--since``/``--until``/``--date``/
    ``--today``) with ``--last-sessions``/``--last-session``: the requested
    number of most-recent sessions (within any date/state/label scope) is turned
    into an effective ``since`` cutoff — the start of the Nth-from-last session —
    so every capture-consuming command inherits the behavior through its existing
    ``since`` plumbing.     ``--last-sessions`` is applied *after* the date/state/label
    scope, so ``--state driving --last-session`` means "the last driving session".

    Also emits a non-fatal stderr note for a ``--state`` token outside the
    profile's vocabulary. Since matching is by token, a typo can only ever match
    nothing, and silently returning an empty result reads as "no such captures"
    rather than "no such state". Every capture-consuming command routes its scope
    through here, so the note lands once for all of them.
    """
    since, until, err = resolve_date_bounds(args)
    if err:
        return since, until, err
    state = getattr(args, "state", None)
    if state:
        from .states import allowed_states, unknown_state_tokens

        unknown = unknown_state_tokens(state)
        if unknown:
            print(
                f"  note: --state {', '.join(unknown)} not in this profile's state "
                f"vocabulary ({', '.join(sorted(allowed_states()))}) — nothing can match.",
                file=sys.stderr,
            )
    n = getattr(args, "last_sessions", None)
    if n:
        label = getattr(args, "label", None)
        starts = _session_starts(since, until, state, label, captures_dir)
        if starts:
            since = starts[-n] if n <= len(starts) else starts[0]
    return since, until, None
