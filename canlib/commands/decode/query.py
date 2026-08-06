"""Turning ``decode``'s selection arguments into concrete work.

Everything between "the user typed something" and "we have payloads to decode":
expanding the QUERY mini-language to ``(ECU, PID)`` targets, narrowing the loaded
captures to the requested scope, and building the ``--try`` candidate signals and
``--corr`` reference. Pure — no printing, no capture decoding — so each piece is
testable on its own.
"""

from __future__ import annotations

from canlib.capture_dates import filter_by_date_range, filter_by_text

# Analysis modes that are inherently single-PID (they bind to one PID's byte
# layout / build one signal). A multi-PID query is rejected for these.
SINGLE_PID_FLAGS = ("corr", "plot", "stats", "discriminate", "find_mirrors", "dump_bytes")


def scope_captures(
    entries: list[dict],
    *,
    since=None,
    until=None,
    state=None,
    label=None,
    first=None,
    last=None,
) -> list[dict]:
    """Apply date/state/label range and first/last slicing to loaded captures.

    Date/text filters run first (they define *what* matches); ``first``/``last``
    then slice the chronologically-ordered survivors. ``first`` and ``last`` are
    applied in that order, so combining them yields the first ``first`` then its
    last ``last`` (rarely useful, but well-defined). Entries are assumed already
    in capture (chronological) order from :func:`capture_store.load_pid_captures`.
    """
    entries = filter_by_date_range(entries, since, until)
    entries = filter_by_text(entries, state=state, label=label)
    if first is not None and first >= 0:
        entries = entries[:first]
    if last is not None and last >= 0:
        entries = entries[-last:] if last else []
    return entries


def parse_try_expr(arg: str) -> tuple[str, str, str]:
    """Parse a ``--try`` argument ``NAME[:unit]=EXPR`` into (name, unit, expr).

    The split is on the first ``=`` so expressions may contain ``:`` (e.g.
    ``[S10:S11]``); an optional unit is taken from ``NAME:unit`` on the left.
    """
    left, sep, expr = arg.partition("=")
    if not sep or not left.strip() or not expr.strip():
        raise ValueError(f"invalid --try {arg!r} (expected NAME[:unit]=EXPR)")
    name, _, unit = left.partition(":")
    if not name.strip():
        raise ValueError(f"invalid --try {arg!r} (empty parameter name)")
    return name.strip(), unit.strip(), expr.strip()


def build_try_params(try_args: list[str]) -> dict:
    """Build synthetic (unverified, candidate) parameter defs from ``--try`` args."""
    params: dict[str, dict] = {}
    for arg in try_args:
        name, unit, expr = parse_try_expr(arg)
        params[name] = {"expression": expr, "unit": unit, "verified": False, "candidate": True}
    return params


def resolve_ref(ref: str, param_names: list[str]) -> str | None:
    """Case-insensitively resolve a --corr reference to an actual param name."""
    for n in param_names:
        if n.upper() == ref.upper():
            return n
    return None


def resolve_targets(
    query_str: str, ecu_index: dict, *, tolerate_missing: bool
) -> tuple[list[tuple[str, str]], str | None]:
    """Expand a mini-language QUERY to concrete ``(ECU, PID)`` pairs (upper-cased).

    Each selector is matched (exact, or prefix/suffix) against the ECU's
    *defined* PIDs. A selector naming a single explicit PID that matches nothing
    defined is still kept as a literal target, so ``decode_one`` can emit its
    "PID not found" guidance (or, under ``--try``/``--plot``, probe the undefined
    PID).
    Returns ``(targets, error)``; ``error`` is a message when nothing resolved.
    """
    from canlib.commands.captures.query import _parse_query
    from canlib.query import QueryError

    try:
        q = _parse_query(query_str)
    except QueryError as e:
        return [], f"invalid query: {e}"

    targets: list[tuple[str, str]] = []
    seen: set[tuple[str, str]] = set()
    unmatched: list[str] = []
    for sel in q.selectors:
        ecu = sel.ecu.upper()
        defined = sorted(ecu_index.get(ecu, {}).get("pids", {}))
        matched = [p.upper() for p in defined if sel.matches_pid(p)]
        if not matched:
            if len(sel.pids) == 1:
                matched = [sel.pids[0].upper()]  # literal (not-found msg / --try)
            else:
                unmatched.append(str(sel))
                continue
        for p in matched:
            key = (ecu, p)
            if key not in seen:
                seen.add(key)
                targets.append(key)

    if not targets:
        avail = ", ".join(sorted(ecu_index))
        detail = f" (selectors matched nothing: {', '.join(unmatched)})" if unmatched else ""
        return [], f"no ECU/PID matched {query_str!r}{detail}. Available ECUs: {avail}"
    return targets, None


def tolerates_missing_pid(args) -> bool:
    """Whether this invocation may target a PID that has no definitions yet.

    The byte-level modes read the captured payload directly, so a captured but
    undefined PID is a legitimate target for them; the value-centric views have
    nothing to show and should report the miss instead.
    """
    return bool(
        args.try_expr or args.plot or args.find_mirrors or args.dump_bytes or args.discriminate
    )
