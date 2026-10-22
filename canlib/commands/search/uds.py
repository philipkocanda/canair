"""``search uds`` — find a string, VIN or byte pattern anywhere in the diagnostic captures.

Every other offline command needs a target (which ECU, which PID) before it will say
anything, so a part number or version string is only findable if you already know
which of ~55 identity DIDs it landed in. This one asks the whole corpus about
*content*: with a PATTERN it finds the needle, without one it inventories every
plausible string.
"""

from __future__ import annotations

import sys

from canlib import ansi
from canlib.capture_dates import filter_by_date_range, filter_by_text, resolve_scope_bounds
from canlib.capture_store import load_all_captures
from canlib.notation import resolve_notation
from canlib.profile import active
from canlib.query import QueryError

from .findings import (
    Finding,
    find_in_definitions,
    find_in_payloads,
    find_in_text,
    inventory_payloads,
)
from .haystack import identity_rows, payload_rows, text_rows
from .needle import (
    DEFAULT_MIN_LEN,
    IN_ALL,
    IN_PAYLOAD,
    IN_TEXT,
    Needle,
    UsageError,
    build_needle,
)
from .render import dump_json, finding_dict, print_table, print_truncation

NAME = "search"


def _error(message: str) -> int:
    print(f"error: {message}", file=sys.stderr)
    return 2


def _definition_findings(needle: Needle, ecus_filter: set[str] | None) -> list[Finding]:
    import yaml

    from canlib.pids import load_pids

    if needle.text is None:
        return []
    try:
        ecus = load_pids().get("ecus") or {}
    except (OSError, yaml.YAMLError) as e:
        # The captures were searched already; one unreadable definition file should
        # cost the --defs part of the answer, loudly, not the whole of it.
        print(f"warning: --defs skipped, the ecus/ definitions did not load: {e}", file=sys.stderr)
        return []
    return find_in_definitions(identity_rows(ecus, only=ecus_filter), needle.text)


def _scan(args, needle: Needle | None, entries: list, ecus_filter: set[str] | None):
    wants_payload = args.in_ in (IN_PAYLOAD, IN_ALL)
    wants_text = args.in_ in (IN_TEXT, IN_ALL)
    if needle is None:
        min_len = DEFAULT_MIN_LEN if args.min_len is None else args.min_len
        return inventory_payloads(
            payload_rows(entries), min_len=min_len, plausible_only=not args.all_runs
        )
    findings: list[Finding] = []
    if wants_payload:
        findings += find_in_payloads(
            payload_rows(entries),
            text_spans=needle.payload_text,
            byte_spans=needle.data,
            match_key=needle.match_key,
        )
    if wants_text and needle.text is not None:
        findings += find_in_text(text_rows(entries), needle.text)
    if args.defs:
        findings += _definition_findings(needle, ecus_filter)
    return findings


def _apply_query(args, entries: list) -> tuple[list, set[str] | None, str | None]:
    """Narrow ``entries`` to the ``--query`` selection: ``(entries, ecus, error)``."""
    if not args.query:
        return entries, None, None
    from canlib.commands.captures.query import _parse_query

    try:
        query = _parse_query(args.query)
    except QueryError as e:
        return entries, None, f"bad --query: {e}"
    matched, empty = query.filter(
        entries, ecu_of=lambda e: e["ecu"], pid_of=lambda e: str(e["pid"])
    )
    for selector in empty:
        print(f"note: no captures matched selector '{selector}'", file=sys.stderr)
    return matched, {s.ecu for s in query.selectors}, None


def run(args) -> int:
    since, until, err = resolve_scope_bounds(args)
    if err:
        return _error(err)
    try:
        needle = build_needle(args)
    except UsageError as e:
        return _error(str(e))

    entries = filter_by_text(
        filter_by_date_range(load_all_captures(), since, until),
        state=args.state,
        label=args.label,
    )
    entries, ecus_filter, err = _apply_query(args, entries)
    if err:
        return _error(err)

    findings = _scan(args, needle, entries, ecus_filter)

    total = len(findings)
    truncated = args.limit > 0 and total > args.limit
    shown = findings[: args.limit] if truncated else findings
    mode = "inventory" if needle is None else "needle"
    label = "plausible strings" if needle is None else needle.label

    profile = active().name
    if args.json:
        dump_json(
            {
                "profile": profile,
                "mode": mode,
                "pattern": label,
                "query": args.query or None,
                "matched": total,
                "hits": sum(f.hits for f in findings),
                "shown": len(shown),
                "truncated": truncated,
                "limit": args.limit,
                "findings": [finding_dict(f, profile) for f in shown],
            }
        )
        return 0 if findings else 1

    if not findings:
        print(f"  No matches for {label}." if needle else "  No plausible strings found.")
        return 1

    hits = sum(f.hits for f in findings)
    print(
        f"\n  {ansi.BOLD}{label}{ansi.RESET} — {total} location{'s' if total != 1 else ''}, "
        f"{hits} hit{'s' if hits != 1 else ''}\n"
    )
    print_table(shown, notation=resolve_notation(args.notation), profile=profile)
    if truncated:
        print_truncation(len(shown), total)
    print()
    return 0
