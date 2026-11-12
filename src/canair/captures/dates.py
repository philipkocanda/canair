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

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from pathlib import Path
from typing import Any

from canair.captures.store import load_all_captures
from canair.profile.model import Profile
from canair.render import messages
from canair.states.selectors import state_matcher, unknown_state_tokens
from canair.states.vocabulary import allowed_states

# NOTE on the scope filters below: they are deliberately shape-agnostic. Each
# reads only a handful of keys (date/time/vehicle_states/label) and is applied
# both to full `CaptureEntry` rows and to the slimmer row
# `capture_store.load_pid_captures` reshapes to. The `_Row` type parameter keeps
# them honest *and* preserves the caller's row type through the filter, so a
# precisely-typed input doesn't degrade to `list[dict]` on the way out.

__all__ = [
    "Scope",
    "entry_date",
    "entry_datetime",
    "filter_by_date_range",
    "filter_by_text",
    "resolve_date_bounds",
    "resolve_scope_bounds",
]


@dataclass(frozen=True)
class Scope:
    """The scope flags a command was given, as values (the CLI reads them off its parser).

    ``day`` is ``--date`` (a whole day), ``today`` shorthand for ``day=<today>``; ``last_sessions`` keeps
    only the most recent N recorded sessions in the rest of the scope.
    """

    since: date | datetime | None = None
    until: date | datetime | None = None
    day: date | None = None
    today: bool = False
    last_sessions: int | None = None
    state: str | Sequence[str] | None = None
    label: str | None = None


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


def _session_key(entry: Mapping[str, Any]) -> tuple[str, int]:
    """The recorded session an entry belongs to.

    Keyed by full path: a layered profile's base and overlay share date file names, and a
    bare name would fold their sessions together.
    """
    return (entry.get("_path") or entry.get("file", ""), entry.get("_session_idx", 0))


def session_anchors(entries: Sequence[Mapping[str, Any]]) -> dict[tuple[str, int], datetime]:
    """Where each session sits on the timeline, to place the captures that have no time.

    A session with timed captures starts at its earliest one. A session with none (a scan,
    an identity read, an import) is placed just after everything recorded before it in the
    same file, because a day's sessions are appended in the order they were recorded; the
    first session of a file with no earlier timed one starts at midnight. Sessions without a
    usable date have no place and are left out.
    """
    first: dict[tuple[str, int], datetime] = {}
    last: dict[tuple[str, int], datetime] = {}
    day: dict[tuple[str, int], date] = {}
    for e in entries:
        key = _session_key(e)
        d = entry_date(e)
        if d is None:
            continue
        day.setdefault(key, d)
        dt = entry_datetime(e)
        if dt is not None:
            first[key] = min(first.get(key, dt), dt)
            last[key] = max(last.get(key, dt), dt)

    anchors: dict[tuple[str, int], datetime] = {}
    latest: dict[str, datetime] = {}
    for key in sorted(day):
        path = key[0]
        if key in first:
            anchors[key] = first[key]
            latest[path] = max(latest.get(path, last[key]), last[key])
        else:
            after = latest.get(path)
            anchors[key] = (
                after + timedelta(microseconds=1) if after else _as_lower_datetime(day[key])
            )
            latest[path] = anchors[key]
    return anchors


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
    time-aware against each capture's ``date``+``time``. A capture with no usable
    timestamp is placed where its session starts (:func:`session_anchors`), so a session of
    scan or import rows is kept or dropped as a whole, like a timed one.
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
    anchors: dict[tuple[str, int], datetime] | None = None
    for e in entries:
        dt = entry_datetime(e)
        if dt is None:
            if anchors is None:
                anchors = session_anchors(entries)
            dt = anchors.get(_session_key(e))
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
    *,
    profile: Profile,
) -> list[Row]:
    """Keep entries whose ``vehicle_states``/``label`` match the selector.

    ``state`` is matched by *token* against the capture's resolved
    ``vehicle_states``, expanded through the profile's ``implies:`` hierarchy —
    see :func:`canair.states.selectors.state_matcher` for the alternative/conjunctive grammar. It
    reads the per-capture value that ``capture_store`` resolved from the
    session's ``state_spans``, so a session that charged and then drove no longer
    returns its driving captures under ``--state CHARGING``.

    ``label`` stays a case-insensitive substring, matched against both the
    session label (stored as ``session_label`` by ``captures`` and ``label`` by
    ``decode``) and any per-capture ``label``. Both filters are ANDed; ``None``
    means "don't filter on this field".
    """

    if not state and not label:
        return entries
    matches_state = state_matcher(state, profile=profile) if state else None
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


def resolve_date_bounds(
    scope: Scope,
) -> tuple[date | datetime | None, date | datetime | None, str | None]:
    """Resolve ``--date``/``--since``/``--until``/``--today`` into ``(since, until, error)``.

    ``--date`` is shorthand for an equal since/until pair (a whole day) and
    ``--today`` for ``--date <today>``; both are mutually exclusive with
    ``--since``/``--until``. ``--since``/``--until`` may each be a ``date`` or a
    ``datetime`` (see ``parse_iso_datetime``). Returns an error message string
    (for the caller to print and exit non-zero) instead of raising, or ``None``
    on success.
    """
    date_ = scope.day
    since = scope.since
    until = scope.until
    today = scope.today
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
    *,
    profile: Profile,
) -> list[datetime]:
    """Start instant of each recorded session in scope, chronological.

    A session's start is where :func:`session_anchors` places it: its earliest capture
    timestamp, or, for a session of untimed captures only, just after the session recorded
    before it in the same file. Sessions with no usable date are skipped.
    """
    entries = load_all_captures(captures_dir, profile=profile)
    entries = filter_by_date_range(entries, since, until)
    entries = filter_by_text(entries, state=state, label=label, profile=profile)
    return sorted(session_anchors(entries).values())


def resolve_scope_bounds(
    scope: Scope, captures_dir: Path | None = None, *, profile: Profile
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
    since, until, err = resolve_date_bounds(scope)
    if err:
        return since, until, err
    state = scope.state
    if state:
        unknown = unknown_state_tokens(state, profile=profile)
        if unknown:
            messages.note(
                f"--state {', '.join(unknown)} not in this profile's state "
                f"vocabulary ({', '.join(sorted(allowed_states(profile=profile)))}) — nothing can match."
            )
    n = scope.last_sessions
    if n is not None and n < 1:
        return since, until, "--last-sessions needs a number of 1 or more"
    if n:
        label = scope.label
        starts = _session_starts(since, until, state, label, captures_dir, profile=profile)
        if starts:
            since = starts[-n] if n <= len(starts) else starts[0]
    return since, until, None
