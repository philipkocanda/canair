"""The shared scope flags (``--since``/``--until``/``--date``/``--state``/``--label`` ...).

The flags and their parsing are CLI; the filtering they select is
:mod:`canlib.captures.dates`, which takes a :class:`~canlib.captures.dates.Scope` of plain values.
Every capture-consuming command declares the flags through :func:`add_scope_args` and turns the
parsed namespace into bounds through :func:`resolve_scope_bounds`.
"""

import argparse
from datetime import date, datetime
from pathlib import Path

from canlib.captures import dates
from canlib.captures.dates import Scope

__all__ = [
    "active_scope_flags",
    "add_scope_args",
    "parse_iso_date",
    "parse_iso_datetime",
    "resolve_date_bounds",
    "resolve_scope_bounds",
    "scope_of",
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


def scope_of(args: argparse.Namespace) -> Scope:
    """The scope flags on ``args`` (each optional: a parser may expose only some)."""
    return Scope(
        since=getattr(args, "since", None),
        until=getattr(args, "until", None),
        day=getattr(args, "date", None),
        today=getattr(args, "today", False),
        last_sessions=getattr(args, "last_sessions", None),
        state=getattr(args, "state", None),
        label=getattr(args, "label", None),
    )


def resolve_date_bounds(
    args: argparse.Namespace,
) -> tuple[date | datetime | None, date | datetime | None, str | None]:
    """:func:`canlib.captures.dates.resolve_date_bounds` over the parsed flags."""
    return dates.resolve_date_bounds(scope_of(args))


def resolve_scope_bounds(
    args: argparse.Namespace, captures_dir: Path | None = None
) -> tuple[date | datetime | None, date | datetime | None, str | None]:
    """:func:`canlib.captures.dates.resolve_scope_bounds` over the parsed flags."""
    return dates.resolve_scope_bounds(scope_of(args), captures_dir)


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
