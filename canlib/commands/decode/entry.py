"""``canair decode``'s entry point: validate the arguments, then fan out over targets.

Owns the argument-combination guards and the multi-PID fan-out. The per-PID work
is :func:`canlib.commands.decode.one.decode_one`; this module decides *how many
times* to call it and with what, and is the only place that knows a multi-PID
``--json`` run must yield one array instead of several.
"""

from __future__ import annotations

import json
import sys

from canlib.capture_dates import resolve_scope_bounds
from canlib.pids import build_ecu_index, load_pids

from .one import decode_one
from .query import SINGLE_PID_FLAGS, build_try_params, resolve_targets, tolerates_missing_pid

# Modifier flags that only mean something on top of a base view. Each entry is
# (modifier dest, required dest) and produces "error: --X requires --Y". A table
# rather than a chain of ifs so adding a modifier cannot forget its guard, and so
# the whole set is assertable in one test.
_MODIFIER_REQUIRES: tuple[tuple[str, str], ...] = (
    ("changes_only", "compact"),
    ("group_by", "stats"),
    ("corr_transform", "corr"),
)


def _flag(dest: str) -> str:
    """The user-facing spelling of an argparse dest (``corr_transform`` -> ``--corr-transform``)."""
    return "--" + dest.replace("_", "-")


def check_modifier_flags(args) -> str | None:
    """The first unmet modifier dependency, as a message — or None if all are satisfied.

    Fails loud rather than silently no-op'ing: ``--changes-only`` without
    ``--compact`` used to be accepted and ignored, which reads as "the flag did
    nothing" instead of "you wanted a different base view".
    """
    for modifier, required in _MODIFIER_REQUIRES:
        if getattr(args, modifier, None) and not getattr(args, required, None):
            return f"{_flag(modifier)} requires {_flag(required)}"
    return None


def run(args) -> int:
    from canlib.commands.captures import build_query

    # Friendly guidance when the QUERY is missing.
    if not args.query:
        from canlib.commands._hints import ecu_hint

        print("Specify an ECU and PID to decode, e.g. `canair decode BMS 2101`.\n")
        print(ecu_hint())
        return 2

    query_str = build_query(args.query)

    # Resolve date scoping (--date shorthand for equal since/until; validated here).
    since, until, err = resolve_scope_bounds(args)
    if err:
        print(f"error: {err}", file=sys.stderr)
        return 2

    if (guard := check_modifier_flags(args)) is not None:
        print(f"error: {guard}", file=sys.stderr)
        return 2

    # Build any candidate expressions from --try (validated early for a clean error).
    try:
        try_params = build_try_params(args.try_expr) if args.try_expr else {}
    except ValueError as e:
        print(f"Error: {e}")
        return 1
    candidate_names = set(try_params)

    # Load PID definitions
    pids_data = load_pids()
    ecu_index = build_ecu_index(pids_data)

    targets, terr = resolve_targets(
        query_str, ecu_index, tolerate_missing=tolerates_missing_pid(args)
    )
    if terr:
        print(f"error: {terr}", file=sys.stderr)
        return 1

    # Analysis modes bind to one PID's byte layout; require a single target.
    single_mode = any(getattr(args, f, False) for f in SINGLE_PID_FLAGS) or bool(args.try_expr)
    if single_mode and len(targets) > 1:
        which = ", ".join(f"{e} {p}" for e, p in targets[:6])
        print(
            f"error: this mode requires the query to resolve to a single PID, but "
            f"{query_str!r} matched {len(targets)} ({which}{'…' if len(targets) > 6 else ''}). "
            f"Narrow it, e.g. `canair decode {targets[0][0]}:{targets[0][1]} …`.",
            file=sys.stderr,
        )
        return 2

    if single_mode or len(targets) == 1:
        ecu_key, pid_key = targets[0]
        return decode_one(
            args, ecu_key, pid_key, ecu_index, try_params, candidate_names, since, until
        )

    # Multi-PID: default value-range, --compact, and --json only.
    if args.json:
        collected: list[dict] = []
        for ecu_key, pid_key in targets:
            decode_one(
                args,
                ecu_key,
                pid_key,
                ecu_index,
                try_params,
                candidate_names,
                since,
                until,
                multi=True,
                json_collect=collected,
            )
        json.dump(collected, sys.stdout, indent=2, default=str)
        print()
        return 0

    rc = 0
    for ecu_key, pid_key in targets:
        one = decode_one(
            args,
            ecu_key,
            pid_key,
            ecu_index,
            try_params,
            candidate_names,
            since,
            until,
            multi=True,
        )
        rc = rc or one
    return rc
