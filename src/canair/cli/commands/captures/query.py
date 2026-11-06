#!/usr/bin/env python3
"""Query selection for the ``captures`` command family.

Selecting entries with the query mini-language (alias-aware), resolving their PID definitions,
and the JSON writer the views share. The keying, dedup and session-grouping primitives the diff
and step views build on are :mod:`canair.captures.entries`.

Nothing here is interactive — functions return data (the one exception is
:func:`_gather_query`, which prints a note for selectors that matched nothing).
"""

import sys
from collections.abc import Mapping, Sequence
from typing import Any

from canair.captures.entries import is_hex_payload
from canair.captures.store import (
    PidDefs,
    load_ecu_index,
    resolve_pid_defs,
)
from canair.render import ansi


# ANSI color helpers (shared across the captures command family).
def _dump_json(obj) -> None:
    """Write ``obj`` to stdout as pretty JSON (dates/other objects via str())."""
    import json

    json.dump(obj, sys.stdout, indent=2, default=str)
    print()


# ---------------------------------------------------------------------------
# Query parsing (alias-aware)
# ---------------------------------------------------------------------------


def build_query(tokens: list[str]) -> str:
    """Turn positional CLI tokens into a query string for ``canair.query``.

    Two bare tokens (neither containing ``:``) collapse to the decode.py-style
    ``ECU PID`` form, i.e. ``ECU:PID`` — so ``BMS 2102`` becomes ``BMS:2102``.
    Everything else is space-joined and handed to the mini-language unchanged, so
    ``BMS:2102,2103`` and a quoted ``"VCU:2101 BMS:2101"`` pass straight through.
    """
    if not tokens:
        return ""
    if len(tokens) == 2 and ":" not in tokens[0] and ":" not in tokens[1]:
        return f"{tokens[0]}:{tokens[1]}"
    return " ".join(tokens)


def _parse_query(query):
    """Parse a QUERY and canonicalize selector ECUs (aliases -> primary name).

    So `canair captures SMK` resolves to the SKM module. Falls back to the raw
    parse if the ECU registry is unavailable; :class:`EcuNameCollision` from an
    ambiguous registry is allowed to propagate.
    """
    from canair.query import parse_query

    q = parse_query(query)
    try:
        from canair.ecus.names import build_canonical_name_index

        name_index = build_canonical_name_index()
    except FileNotFoundError:
        return q
    return q.canonicalize_ecus(lambda ecu: name_index.get(ecu, ecu).upper())


# ---------------------------------------------------------------------------
# Query gathering (shared by --diff and --step)
# ---------------------------------------------------------------------------


def _gather_query[R: Mapping[str, Any]](
    entries: Sequence[R], query, *, warn: bool = True
) -> tuple[list[dict], dict[tuple[str, str], PidDefs]]:
    """Select payload captures matching ``query`` (a canair.query string/Query).

    Returns ``(captures, defs)``:
      - ``captures`` — payload-bearing entries matching any selector, sorted
        chronologically ``(date, time)``.
      - ``defs`` — cache mapping ``(ECU_UPPER, PID_UPPER)`` to ``(parameters,
        tx_id)`` for every distinct pair present in ``captures``.

    When ``warn`` is set, prints a note for any selector that matched nothing
    (with an ``ECU:PID`` hint when a bare selector looks like a DID).
    """
    q = _parse_query(query)
    payloads = [e for e in entries if is_hex_payload(e.get("payload"))]
    matched, empty = q.filter(payloads, ecu_of=lambda e: e["ecu"], pid_of=lambda e: e["pid"])

    # Chronological order (date, then time within a session).
    matched.sort(key=lambda e: (str(e.get("date", "")), str(e.get("time", ""))))

    ecu_index = load_ecu_index()
    defs: dict[tuple[str, str], PidDefs] = {}
    for e in matched:
        key = (e["ecu"].upper(), str(e["pid"]).upper())
        if key not in defs:
            defs[key] = resolve_pid_defs(ecu_index, *key)

    if warn and empty:
        from canair.query import looks_like_pid

        known_ecus = {e["ecu"].upper() for e in payloads}
        for sel in empty:
            hint = ""
            # Bare selector whose "ECU" isn't a real ECU but looks like a DID —
            # likely the old `ECU PID` space form; nudge toward `ECU:PID`.
            if not sel.pids and sel.ecu not in known_ecus and looks_like_pid(sel.ecu):
                hint = "  (did you mean to attach it as a PID, e.g. ECU:PID?)"
            print(f"  {ansi.YELLOW}No captures matched selector '{sel}'{ansi.RESET}{hint}")
        avail = ", ".join(sorted(known_ecus))
        print(f"  {ansi.DIM}Available ECUs: {avail}{ansi.RESET}")

    return matched, defs
