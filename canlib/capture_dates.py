#!/usr/bin/env python3
"""Shared date/state/label scoping helpers for capture-consuming commands.

Both ``canair captures`` and ``canair decode`` load capture entries and let the
user narrow them by session date (``--since``/``--until``/``--date``) and by a
substring of the session ``state`` or ``label``. The parsing/filtering logic is
identical, so it lives here and is imported by both to keep their scoping surface
consistent.

Entries are plain dicts; the helpers only read ``date``, ``state``, ``label``
and (for captures) ``session_label`` keys, so any capture-shaped dict works.
"""

import argparse
from datetime import date, datetime

__all__ = [
    "add_scope_args",
    "entry_date",
    "filter_by_date_range",
    "filter_by_text",
    "parse_iso_date",
    "resolve_date_bounds",
]


def parse_iso_date(s: str) -> date:
    """Parse an ``YYYY-MM-DD`` string into a ``date`` (for argparse ``type=``).

    Raises ``argparse.ArgumentTypeError`` on a malformed value so argparse emits
    a clean usage error.
    """
    try:
        return datetime.strptime(s.strip(), "%Y-%m-%d").date()
    except ValueError:
        raise argparse.ArgumentTypeError(f"invalid date {s!r} (expected YYYY-MM-DD)") from None


def entry_date(entry: dict) -> date | None:
    """Parse a capture entry's session ``date`` field, or None if absent/invalid.

    Tolerates a trailing suffix on same-day sessions (e.g. ``2026-04-17-b``) by
    falling back to the leading ``YYYY-MM-DD`` portion, so those captures still
    sort into the correct day when a date filter is active.
    """
    raw = str(entry.get("date", "")).strip()
    if not raw:
        return None
    for candidate in (raw, raw[:10]):
        try:
            return datetime.strptime(candidate, "%Y-%m-%d").date()
        except ValueError:
            continue
    return None


def filter_by_date_range(
    entries: list[dict], since: date | None = None, until: date | None = None
) -> list[dict]:
    """Keep entries whose session date falls within ``[since, until]`` (inclusive).

    Either bound may be ``None`` (open-ended). Entries without a parseable date
    are dropped whenever a bound is active, since they cannot be placed in range.
    """
    if since is None and until is None:
        return entries
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


def filter_by_text(
    entries: list[dict], state: str | None = None, label: str | None = None
) -> list[dict]:
    """Keep entries whose session ``vehicle_states``/``label`` match the substrings.

    Matching is case-insensitive and substring-based against the joined
    ``vehicle_states`` list (the natural unit of drive analysis, e.g. a session
    tagged ``[driving]``). ``label`` is matched against both the session label
    (stored as ``session_label`` by ``captures`` and ``label`` by ``decode``)
    and any per-capture ``label``. Both filters are ANDed; ``None`` means "don't
    filter on this field".
    """
    from .states import join_states

    if not state and not label:
        return entries
    s_needle = state.lower() if state else None
    l_needle = label.lower() if label else None
    out = []
    for e in entries:
        if s_needle is not None and s_needle not in join_states(e.get("vehicle_states")).lower():
            continue
        if l_needle is not None:
            haystack = f"{e.get('session_label', '')} {e.get('label', '')}".lower()
            if l_needle not in haystack:
                continue
        out.append(e)
    return out


def add_scope_args(parser: argparse.ArgumentParser) -> None:
    """Add the shared ``--since/--until/--date`` and ``--state/--label`` scoping flags.

    Kept in one place so ``captures`` and ``decode`` expose an identical scoping
    surface (same flag names, metavars, and help text).
    """
    date_group = parser.add_argument_group(
        "scoping",
        "Restrict to captures within a date range (inclusive, YYYY-MM-DD) "
        "and/or by session state/label substring",
    )
    date_group.add_argument(
        "--since",
        type=parse_iso_date,
        metavar="YYYY-MM-DD",
        help="Only captures on or after this date",
    )
    date_group.add_argument(
        "--until",
        type=parse_iso_date,
        metavar="YYYY-MM-DD",
        help="Only captures on or before this date",
    )
    date_group.add_argument(
        "--date",
        type=parse_iso_date,
        metavar="YYYY-MM-DD",
        help="Only captures on this exact date (shorthand for --since X --until X)",
    )
    date_group.add_argument(
        "--state",
        metavar="SUBSTR",
        help="Only captures whose session vehicle_states contain SUBSTR "
        "(case-insensitive), e.g. --state driving",
    )
    date_group.add_argument(
        "--label",
        metavar="SUBSTR",
        help="Only captures whose session/capture label contains SUBSTR (case-insensitive)",
    )


def resolve_date_bounds(args) -> tuple[date | None, date | None, str | None]:
    """Resolve ``--date``/``--since``/``--until`` into ``(since, until, error)``.

    ``--date`` is shorthand for an equal since/until pair and is mutually
    exclusive with ``--since``/``--until``. Returns an error message string (for
    the caller to print and exit non-zero) instead of raising, or ``None`` on
    success.
    """
    date_ = getattr(args, "date", None)
    since = getattr(args, "since", None)
    until = getattr(args, "until", None)
    if date_ and (since or until):
        return None, None, "--date cannot be combined with --since/--until"
    since = date_ or since
    until = date_ or until
    if since and until and since > until:
        return None, None, f"--since ({since}) is after --until ({until})"
    return since, until, None
