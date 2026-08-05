#!/usr/bin/env python3
"""Decode captured UDS payloads using PID parameter definitions.

Takes an ECU+PID, loads all matching captures, applies WiCAN expressions
from the YAML PID definitions, and reports how each decoded *parameter value*
behaves across the full capture history. Parameter/value-centric and focused
on validating expressions — for payload/byte-level views (hex, byte-diff,
dedup, cross-ECU, dates) use `canair captures` instead.

By default it prints each parameter's value range (min-max, or constant) across
all captures. Use --compact for a chronological one-line-per-capture view, or
--try to test a candidate expression without editing YAML.

Scope which captures are considered with --since/--until/--date (like `canair
captures`) and --state/--label (case-insensitive substring of the session state
or label — the natural unit of drive analysis). --first/--last N slice the
matching captures chronologically.

Examples:
  canair decode BMS 2101              # Value range of every param across captures
  canair decode BMS 2101 --param SOC_BMS SOC_DISP  # Only specific params
  canair decode IGPM 22BC03           # Decode IGPM DID BC03
  canair decode BMS 2101 --verified   # Only verified parameters
  canair decode BMS 2101 --unverified # Only unverified parameters (validation focus)
  canair decode BMS 2101 --compact    # One line per capture (value evolution)
  canair decode ESC 22C101 --state 'MT->KW' --compact --changes-only  # One drive, stationary runs collapsed
  canair decode MCU 2102 --stats --group-by state  # Per-drive-segment statistics
  canair decode VCU 2101 --date 2026-07-22 --last 20  # Last 20 captures of one day
  canair decode BMS 2101 --json       # JSON (per-capture decoded values)
  canair decode MCU 2102 --stats      # Descriptive stats per param (mean/median/stdev/distinct)
  canair decode MCU 2102 --corr MCU_MOTOR_RPM   # Correlate every param vs a known signal
  canair decode MCU 2102 --plot                      # sweep interpretations, find the signal
  canair decode MCU 2102 --plot --corr MCU_MOTOR_RPM # overlay a known signal + live r
  canair decode MCU 2102 --try "TORQUE:Nm=[S12:S13]/100"   # Test a candidate expression
  canair decode MCU 2102 --try "T=[S17:S18]" --corr MCU_MOTOR_RPM  # Validate a candidate by correlation
  canair decode MCU 21F2 --try "X=B9" --try "Y=[S10:S11]"  # Multiple candidates, undefined PID OK
  canair decode BMS 2101 --dump-bytes         # timestamp x byte-offset matrix (CSV, PCI skipped)
  canair decode BMS 2101 --dump-bytes --json  # same matrix as JSON (ad-hoc analysis escape hatch)
"""

import argparse
import json
import sys

from canlib.align import DEFAULT_JOIN_TOL_S, join_nearest
from canlib.capture_dates import (
    add_scope_args,
    filter_by_date_range,
    filter_by_text,
    resolve_scope_bounds,
)
from canlib.commands._decode_calc import (
    _local_series,
    _paired,
    _series,
    axis_group_keys,
    load_cross_ref_series,
)
from canlib.commands._decode_plot import (
    PlotModel,
    cmd_plot,
)
from canlib.commands._decode_render import (
    _dump_bytes,
    print_compact,
    print_correlations,
    print_discriminate,
    print_mirrors,
    print_stats_grouped,
    print_stats_table,
    print_value_ranges,
    scope_banner,
)
from canlib.commands._hints import ecu_completer as _ecu_completer
from canlib.expression import evaluate_expression
from canlib.inspect_bytes import POST_TRANSFORMS
from canlib.keepmode import CHANGES_BANNER, scope_is_keep_changes, scope_is_keep_unique
from canlib.notation import (
    add_notation_arg,
    resolve_notation,
    subfunction_bytes_for_pid,
)
from canlib.pids import build_ecu_index, load_pids
from canlib.stats import METHOD_CHEAT_SHEET as _METHOD_CHEAT_SHEET
from canlib.stats import compute_stats
from canlib.stats import correlation as _correlation

NAME = "decode"
ALIASES = ["dec"]

# ANSI colors
_BOLD = "\033[1m"
_DIM = "\033[2m"
_GREEN = "\033[92m"
_YELLOW = "\033[93m"
_RED = "\033[91m"
_CYAN = "\033[96m"
_RESET = "\033[0m"


def load_captures(ecu: str, pid: str) -> list[dict]:
    """Load all payload captures matching ECU+PID from capture files.

    Thin wrapper over the canonical :func:`capture_store.load_all_captures`
    (one loader for the whole tool), narrowed to a single ECU+PID and reshaped to
    the slim dict decode's views expect: ``{file, date, label, vehicle_states,
    payload, notes, time}``. Capture ``ecu`` addresses are resolved to canonical
    short names by the shared loader.
    """
    from canlib.capture_store import load_all_captures

    entries = []
    for e in load_all_captures():
        if str(e.get("ecu", "")).upper() != ecu.upper():
            continue
        if str(e.get("pid", "")).upper() != pid.upper():
            continue
        if not e.get("payload"):
            continue
        entries.append(
            {
                "file": e.get("file", ""),
                "date": str(e.get("date", "")),
                "label": e.get("session_label", ""),
                "vehicle_states": list(e.get("vehicle_states") or []),
                "payload": e["payload"],
                "notes": e.get("notes", ""),
                "time": e.get("time", ""),
                "keep_mode": e.get("keep_mode", ""),
            }
        )
    return entries


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
    in capture (chronological) order from :func:`load_captures`.
    """
    entries = filter_by_date_range(entries, since, until)
    entries = filter_by_text(entries, state=state, label=label)
    if first is not None and first >= 0:
        entries = entries[:first]
    if last is not None and last >= 0:
        entries = entries[-last:] if last else []
    return entries


def payload_to_wican_bytes(payload_hex: str) -> bytes:
    """Convert raw UDS payload hex to WiCAN frame bytes (with PCI inserted).

    Delegates to the canonical converter in ``byteindex`` (one PCI-reconstruction
    path for the whole tool); kept as a re-export for decode's callers/tests.
    """
    from canlib.byteindex import payload_to_wican_bytes as _to_bytes

    return _to_bytes(payload_hex)


def decode_payload(wican_bytes: bytes, parameters: dict) -> dict[str, dict]:
    """Evaluate all parameter expressions against a WiCAN frame.

    Returns dict: param_name -> {value, expression, unit, verified, error}.

    For a param declaring a ``type:`` (enum/bitmask/ascii/date/bcd/struct) the
    result also carries ``display`` (the rendered typed string) and ``category``
    (a nominal key for categorical stats). ``value`` remains the raw float so
    every numeric consumer (min/max/corr/stats) is unaffected.
    """
    from canlib.decode_value import decode_typed, render

    results = {}
    for name, param in parameters.items():
        expr = param.get("expression", "")
        ptype = (param.get("type") or "numeric").lower()
        if not expr and ptype in ("numeric", "enum", "bitmask", "bcd"):
            continue
        try:
            entry: dict = {
                "expression": expr,
                "unit": param.get("unit", ""),
                "verified": param.get("verified", False),
                "min": param.get("min"),
                "max": param.get("max"),
            }
            if ptype != "numeric":
                dv = decode_typed(param, wican_bytes)
                entry["value"] = dv.raw
                entry["type"] = ptype
                entry["display"] = render(dv, param.get("unit", ""))
                entry["category"] = dv.category()
            else:
                entry["value"] = evaluate_expression(expr, wican_bytes)
            results[name] = entry
        except Exception as e:
            results[name] = {
                "value": None,
                "expression": expr,
                "unit": param.get("unit", ""),
                "verified": param.get("verified", False),
                "error": str(e),
            }
    return results


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


def add_parser(subparsers) -> argparse.ArgumentParser:
    parser = subparsers.add_parser(
        NAME,
        aliases=ALIASES,
        help="Decode captured UDS payloads using PID parameter definitions",
        description="Decode captured UDS payloads using PID parameter definitions.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(__doc__.split("Examples:")[1] if "Examples:" in __doc__ else "")
        + "\n"
        + _METHOD_CHEAT_SHEET,
    )
    parser.add_argument(
        "query",
        nargs="*",
        metavar="QUERY",
        help="ECU/PID selection (mini-language, see canlib/query.py): 'BMS 2101', "
        "'BMS:2101', 'BMS:2101,2102', 'BMS' (all defined PIDs), or a quoted "
        "cross-ECU query 'MCU:2102 VCU:2101'. Multi-PID queries are supported for "
        "the default value-range, --compact and --json views; the analysis modes "
        "(--corr/--plot/--stats/--discriminate/--find-mirrors/--try/--dump-bytes) "
        "require the query to resolve to a single PID.",
    ).completer = _ecu_completer
    parser.add_argument(
        "--param",
        action="extend",
        nargs="+",
        metavar="NAME",
        help="Show only specific parameters (repeatable and/or space-separated: "
        "--param A B or --param A --param B)",
    )
    parser.add_argument("--verified", action="store_true", help="Show only verified parameters")
    parser.add_argument("--unverified", action="store_true", help="Show only unverified parameters")
    parser.add_argument(
        "--json", action="store_true", help="Output as JSON (per-capture decoded values)"
    )
    parser.add_argument(
        "--compact",
        action="store_true",
        help="One line per capture (chronological param=value pairs)",
    )
    parser.add_argument(
        "--changes-only",
        "-c",
        action="store_true",
        help="With --compact: skip rows where all shown params are "
        "unchanged from the previous row (collapses stationary runs)",
    )
    parser.add_argument(
        "--stats",
        action="store_true",
        help="Descriptive statistics per param (n, distinct, mean, median, stdev)",
    )
    parser.add_argument(
        "--group-by",
        choices=["state", "vehicle_states"],
        metavar="FIELD",
        help="With --stats: compute statistics per session FIELD "
        "(currently 'state') instead of pooling all captures",
    )
    parser.add_argument(
        "--discriminate",
        metavar="AXIS",
        help="Rank params/bytes by how cleanly they separate across AXIS groups "
        "(F = between/within variance; Cramér's V for typed params) — finds "
        "axis-dependent signals a driving correlation misses. AXIS is 'state' "
        "(the vehicle power state) or a cross-signal ECU:PID:PARAM to group by "
        "(e.g. HVAC:220102:HVAC_COMPRESSOR_ON — which byte separates on from off)",
    )
    parser.add_argument(
        "--find-mirrors",
        action="store_true",
        help="Report byte positions that are exactly equal across all captures "
        "(redundant status mirrors / unit-variants); add --bits for bit-level",
    )
    parser.add_argument(
        "--bits",
        action="store_true",
        help="With --find-mirrors: compare individual bits (Bn:k). "
        "With --discriminate: also rank individual toggling bits across the axis",
    )
    parser.add_argument(
        "--bytes",
        action="store_true",
        help="With --discriminate: also rank every varying raw byte (Bn), not "
        "just defined params — finds axis-dependent bytes without a --try",
    )
    parser.add_argument(
        "--first", type=int, metavar="N", help="Only the first N matching captures (chronological)"
    )
    parser.add_argument(
        "--last", type=int, metavar="N", help="Only the last N matching captures (chronological)"
    )
    parser.add_argument(
        "--corr",
        metavar="PARAM",
        help="Correlate every param (incl. --try) against PARAM (Pearson r). "
        "PARAM may be a local param name, or a cross-signal reference "
        "ECU:PID:PARAM or ECU:PID:EXPR (e.g. ESC:22C101:REAL_SPEED_KMH) which is "
        "time-aligned by nearest timestamp.",
    )
    parser.add_argument(
        "--join-tol",
        type=float,
        default=None,
        metavar="SECONDS",
        help="Nearest-timestamp join window for a cross-signal --corr (default 2.5s)",
    )
    parser.add_argument(
        "--corr-transform",
        choices=list(POST_TRANSFORMS),
        metavar="MODE",
        help="Transform the --corr reference before pairing "
        "(raw/delta/abs/cumsum/normalize/smooth) — e.g. --corr-transform delta "
        "to test whether a signal tracks a reference's RATE rather than its level",
    )
    parser.add_argument(
        "--method",
        choices=["pearson", "spearman", "cramers_v", "mutual_info"],
        default="pearson",
        help="Coefficient for --corr: pearson (linear, default) or spearman "
        "(rank — catches monotone-but-nonlinear/quantized/saturating links), or "
        "the categorical cramers_v / mutual_info (nominal association — for "
        "mode/flag/enum references where numeric spacing is meaningless)",
    )
    parser.add_argument(
        "--plot",
        action="store_true",
        help="Interactive signal explorer: sweep byte interpretations "
        "(u8/i16/f32/... and endianness) and params, plot across captures, "
        "apply transforms (delta/abs/normalize/...), zoom/pan the x-axis, "
        "overlay a --corr signal, and flag bytes already mapped by a param",
    )
    parser.add_argument(
        "--try",
        dest="try_expr",
        action="append",
        metavar="NAME[:unit]=EXPR",
        help="Evaluate a candidate expression against captures without editing "
        "YAML (repeatable; works even if the PID has no params defined yet)",
    )
    parser.add_argument(
        "--dump-bytes",
        dest="dump_bytes",
        action="store_true",
        help="Emit a timestamp × byte-offset matrix (one row per capture) instead "
        "of decoding params — the escape hatch for ad-hoc byte analysis. CSV by "
        "default; add --json for JSON. PCI framing bytes are skipped unless "
        "--include-pci. Honours --notation for column labels and all scope flags",
    )
    parser.add_argument(
        "--include-pci",
        dest="include_pci",
        action="store_true",
        help="With --dump-bytes: include ISO-TP PCI framing bytes (skipped by default)",
    )
    parser.add_argument(
        "--signed",
        dest="dump_signed",
        action="store_true",
        help="With --dump-bytes: render each data byte as a signed value (-128..127) "
        "with an Snn column header, instead of the default unsigned Bnn (0..255). "
        "Use when a byte is the high half of a signed value (a 0xFF near-zero baseline "
        "correlates poorly unsigned but cleanly signed)",
    )
    add_notation_arg(parser)
    add_scope_args(parser)
    parser.set_defaults(func=run)
    return parser


def _plot_pid_options() -> list[tuple[str, str]]:
    """Distinct ``(ECU, PID)`` pairs that have payload captures, for the --plot switcher."""
    from canlib.capture_store import load_all_captures

    seen: dict[tuple[str, str], None] = {}
    try:
        for e in load_all_captures():
            if not e.get("payload"):
                continue
            ecu = str(e.get("ecu", "")).upper()
            pid = str(e.get("pid", "")).upper()
            if ecu and pid:
                seen.setdefault((ecu, pid), None)
    except Exception:
        return []
    return sorted(seen)


def _build_plot_model(args, ecu: str, pid: str) -> PlotModel | None:
    """(Re)build a :class:`PlotModel` for ``ecu``/``pid`` reusing ``args`` scope.

    Used by the --plot TUI's in-place PID switch. Carries the date/state/label
    scope, but not --try/--corr (those were bound to the originally-selected PID).
    Returns None when the target has no plottable captures.
    """
    ecu_key = ecu.upper()
    pid_key = pid.upper()
    since, until, err = resolve_scope_bounds(args)
    if err:
        return None
    scope = {
        "since": since,
        "until": until,
        "state": args.state,
        "label": args.label,
        "first": args.first,
        "last": args.last,
    }
    pids_data = load_pids()
    ecu_index = build_ecu_index(pids_data)
    parameters: dict = {}
    if ecu_key in ecu_index:
        parameters = ecu_index[ecu_key]["pids"].get(pid_key, {}).get("parameters", {}) or {}
    defined_params = dict(parameters)

    captures = scope_captures(load_captures(ecu_key, pid_key), **scope)
    all_results: list[dict] = []
    for cap in captures:
        try:
            wican_bytes = payload_to_wican_bytes(cap["payload"])
        except Exception as e:
            all_results.append({"capture": cap, "decoded": {}, "error": str(e)})
            continue
        all_results.append({"capture": cap, "decoded": decode_payload(wican_bytes, parameters)})

    model = PlotModel(
        all_results,
        list(parameters.keys()),
        parameters,
        set(),
        None,
        ecu_key,
        pid_key,
        defined_params=defined_params,
    )
    return None if model.empty else model


# Analysis modes that are inherently single-PID (they bind to one PID's byte
# layout / build one signal). A multi-PID query is rejected for these.
_SINGLE_PID_FLAGS = ("corr", "plot", "stats", "discriminate", "find_mirrors", "dump_bytes")


def _resolve_targets(
    query_str: str, ecu_index: dict, *, tolerate_missing: bool
) -> tuple[list[tuple[str, str]], str | None]:
    """Expand a mini-language QUERY to concrete ``(ECU, PID)`` pairs (upper-cased).

    Each selector is matched (exact, or prefix/suffix) against the ECU's
    *defined* PIDs. A selector naming a single explicit PID that matches nothing
    defined is still kept as a literal target, so ``_decode_one`` can emit its
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


def run(args) -> int:
    from canlib.commands.captures import build_query

    # Friendly guidance when the QUERY is missing.
    if not args.query:
        from canlib.commands._hints import ecu_hint

        print("Specify an ECU and PID to decode, e.g. `canair decode BMS 2101`.\n")
        print(ecu_hint())
        return 2

    query_str = build_query(args.query)

    # --plot and --try tolerate a not-yet-defined ECU/PID (raw byte inspection).
    tolerate_missing = (
        bool(args.try_expr)
        or args.plot
        or args.find_mirrors
        or args.dump_bytes
        or args.discriminate
    )

    # Resolve date scoping (--date shorthand for equal since/until; validated here).
    since, until, err = resolve_scope_bounds(args)
    if err:
        print(f"error: {err}", file=sys.stderr)
        return 2

    # Modifier flags depend on their base view; fail loud rather than silently no-op.
    if args.changes_only and not args.compact:
        print("error: --changes-only requires --compact", file=sys.stderr)
        return 2
    if args.group_by and not args.stats:
        print("error: --group-by requires --stats", file=sys.stderr)
        return 2
    if args.corr_transform and not args.corr:
        print("error: --corr-transform requires --corr", file=sys.stderr)
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

    targets, terr = _resolve_targets(query_str, ecu_index, tolerate_missing=tolerate_missing)
    if terr:
        print(f"error: {terr}", file=sys.stderr)
        return 1

    # Analysis modes bind to one PID's byte layout; require a single target.
    single_mode = any(getattr(args, f, False) for f in _SINGLE_PID_FLAGS) or bool(args.try_expr)
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
        return _decode_one(
            args, ecu_key, pid_key, ecu_index, try_params, candidate_names, since, until
        )

    # Multi-PID: default value-range, --compact, and --json only.
    if args.json:
        collected: list[dict] = []
        for ecu_key, pid_key in targets:
            _decode_one(
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
        one = _decode_one(
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


def _decode_one(
    args,
    ecu_key: str,
    pid_key: str,
    ecu_index: dict,
    try_params: dict,
    candidate_names: set,
    since,
    until,
    *,
    multi: bool = False,
    json_collect: list | None = None,
) -> int:
    """Decode a single ECU+PID (the original per-PID pipeline).

    ``multi`` marks a call that is one of several in a multi-PID query (used to
    keep the "no parameters/captures" notes terse and to continue past a miss);
    ``json_collect`` (when set) collects this PID's per-capture JSON into a shared
    list instead of dumping it, so a multi-PID ``--json`` yields one array.
    """
    # --plot and --try tolerate a not-yet-defined ECU/PID (raw byte inspection).
    tolerate_missing = (
        bool(args.try_expr)
        or args.plot
        or args.find_mirrors
        or args.dump_bytes
        or args.discriminate
    )

    # Resolve defined parameters. With --try we tolerate an unknown ECU/PID so a
    # brand-new PID (captured but not yet defined) can still be probed.
    parameters: dict = {}
    if ecu_key in ecu_index:
        ecu_pids = ecu_index[ecu_key]["pids"]
        if pid_key in ecu_pids:
            parameters = ecu_pids[pid_key]["parameters"]
        elif not tolerate_missing:
            print(
                f"PID '{pid_key}' not found for {ecu_key}. Available: {', '.join(sorted(ecu_pids))}"
            )
            return 1
    elif not tolerate_missing:
        print(f"ECU '{ecu_key}' not found in ecus/. Available: {', '.join(sorted(ecu_index))}")
        return 1

    # Full (unfiltered) defined params for this PID — used by --plot to flag
    # bytes that are already mapped, independent of --param/--verified filters.
    defined_params = dict(parameters)

    # Filter parameters (applies to *defined* params only; --try params are always shown)
    if args.param:
        filter_names = {n.upper() for n in args.param}
        parameters = {k: v for k, v in parameters.items() if k.upper() in filter_names}
        missing = filter_names - {k.upper() for k in parameters}
        if missing:
            print(f"Warning: parameters not found: {', '.join(sorted(missing))}")
    if args.verified:
        parameters = {k: v for k, v in parameters.items() if v.get("verified", False)}
    if args.unverified:
        parameters = {k: v for k, v in parameters.items() if not v.get("verified", False)}

    # Merge candidate expressions in (override a defined param on name clash).
    if try_params:
        parameters = {**parameters, **try_params}

    # Scope arguments shared by every capture load in this run (date/state/label
    # range + first/last slice). Resolved once so all paths stay consistent.
    scope = {
        "since": since,
        "until": until,
        "state": args.state,
        "label": args.label,
        "first": args.first,
        "last": args.last,
    }
    scoped = any(v is not None for v in scope.values())

    # Raw byte/bit analyses (--discriminate/--find-mirrors with --bytes/--bits)
    # rank straight off the captured payloads and need no defined parameters —
    # let them through the "nothing defined" early-exit below.
    raw_byte_mode = bool((args.discriminate or args.find_mirrors) and (args.bytes or args.bits))

    if not parameters and not args.plot and not args.dump_bytes and not raw_byte_mode:
        # Be capture-aware and split the two cases that used to collapse into one
        # misleading message: filters excluded everything vs. nothing defined yet.
        # (This is a terminating error path, so loading captures here doesn't
        # double-load — the normal path at load_captures() below is only reached
        # when there are parameters to decode.)
        caps = scope_captures(load_captures(ecu_key, pid_key), **scope)
        if multi:
            # Terse one-liner per PID inside a multi-PID query (keep it scannable).
            note = (
                "filtered out"
                if defined_params
                else (f"no params defined ({len(caps)} captures)" if caps else "no params/captures")
            )
            print(f"\n{_BOLD}{ecu_key} {pid_key}{_RESET} — {_DIM}{note}{_RESET}")
            return 1
        if defined_params:
            # Params exist for this PID; the active --param/--verified/--unverified
            # filters just excluded them all.
            print(
                f"No parameters match the filter criteria "
                f"({len(defined_params)} defined for {ecu_key} {pid_key})."
            )
        elif caps:
            # Captured but not yet decoded — signpost the byte-level tools.
            print(
                f"{_BOLD}{ecu_key} {pid_key}{_RESET} — no parameters defined yet, "
                f"but {len(caps)} capture(s) exist."
            )
            print(
                f"  {_DIM}·{_RESET} Inspect raw bytes:  canair captures {ecu_key} {pid_key} --diff"
            )
            print(f"  {_DIM}·{_RESET} Explore signals:    canair decode {ecu_key}:{pid_key} --plot")
            print(
                f"  {_DIM}·{_RESET} Test a candidate:   "
                f'canair decode {ecu_key}:{pid_key} --try "NAME:unit=EXPR"'
            )
        else:
            # Neither defined nor captured.
            print(f"No parameters defined and no captures found for {ecu_key} {pid_key}.")
        return 1

    # Resolve the --corr reference. A reference containing ':' is a cross-signal
    # ECU:PID:PARAM|EXPR loaded from another ECU/PID and time-aligned; otherwise
    # it's a local param name paired by shared payload.
    corr_ref = None
    cross_ref_series = None
    cross_ref_label = None
    if args.corr:
        if ":" in args.corr:
            try:
                cross_ref_series, cross_ref_label = load_cross_ref_series(
                    args.corr, scope=scope, tol_s=args.join_tol
                )
            except ValueError as e:
                print(f"--corr error: {e}", file=sys.stderr)
                return 1
            corr_ref = cross_ref_label
        else:
            corr_ref = resolve_ref(args.corr, list(parameters.keys()))
            if corr_ref is None:
                print(
                    f"--corr reference '{args.corr}' not found. Available: {', '.join(parameters)}"
                )
                return 1

    # Load captures (with any date/state/label/first/last scoping applied).
    captures = scope_captures(load_captures(ecu_key, pid_key), **scope)
    if not captures:
        if multi:
            print(f"\n{_BOLD}{ecu_key} {pid_key}{_RESET} — {_DIM}no captures in scope{_RESET}")
            return 1
        if scoped:
            print(f"No captures for {ecu_key} PID {pid_key} match the scope filters.")
        else:
            print(f"No captures found for {ecu_key} PID {pid_key}.")
        return 1

    # Decode all captures
    all_results: list[dict] = []
    for cap in captures:
        try:
            wican_bytes = payload_to_wican_bytes(cap["payload"])
        except Exception as e:
            all_results.append(
                {
                    "capture": cap,
                    "decoded": {},
                    "error": f"payload parse error: {e}",
                }
            )
            continue

        decoded = decode_payload(wican_bytes, parameters)
        all_results.append(
            {
                "capture": cap,
                "decoded": decoded,
            }
        )

    # Byte-matrix export: timestamp x byte-offset, independent of param defs.
    if args.dump_bytes:
        return _dump_bytes(
            all_results,
            ecu_key,
            pid_key,
            as_json=args.json,
            include_pci=args.include_pci,
            notation=resolve_notation(args.notation),
            sub_bytes=subfunction_bytes_for_pid(pid_key),
            signed=args.dump_signed,
        )

    # Interactive signal explorer (byte interpretations + params + transforms).
    if args.plot:
        cmd_plot(
            all_results,
            list(parameters.keys()),
            parameters,
            candidate_names,
            corr_ref,
            ecu_key,
            pid_key,
            defined_params=defined_params,
            reload_pid=lambda e, p: _build_plot_model(args, e, p),
            pid_options=_plot_pid_options(),
        )
        return 0

    if args.json:
        param_names = list(parameters.keys())
        if args.stats or corr_ref:
            # Aggregate JSON: per-param statistics and/or correlations vs the ref.
            out: dict = {}
            if args.stats:
                out["stats"] = {
                    name: {
                        k: v
                        for k, v in compute_stats(_series(all_results, name)).items()
                        if k != "values"
                    }
                    for name in param_names
                    if _series(all_results, name)
                }
            if corr_ref:
                out["reference"] = corr_ref
                out["method"] = args.method
                out["correlations"] = {}
                if cross_ref_series is not None:
                    tol = args.join_tol if args.join_tol is not None else DEFAULT_JOIN_TOL_S
                    out["join_tol_s"] = tol
                    for name in param_names:
                        local = _local_series(all_results, name)
                        xs, ys, n = join_nearest(cross_ref_series, local, tol_s=tol)
                        out["correlations"][name] = {"r": _correlation(xs, ys, args.method), "n": n}
                else:
                    for name in param_names:
                        if name == corr_ref:
                            continue
                        xs, ys = _paired(all_results, corr_ref, name)
                        out["correlations"][name] = {
                            "r": _correlation(xs, ys, args.method),
                            "n": len(xs),
                        }
            json.dump(out, sys.stdout, indent=2, default=str)
            print()
            return 0
        # JSON output — per-capture decoded values (payload-level data lives in
        # query-captures.py; decode.py is parameter/value-centric).
        out = []
        for r in all_results:
            entry = {
                "date": r["capture"]["date"],
                "vehicle_states": r["capture"].get("vehicle_states") or [],
                "file": r["capture"]["file"],
            }
            if r["capture"].get("time"):
                entry["time"] = r["capture"]["time"]
            entry["parameters"] = {}
            for name, d in r["decoded"].items():
                entry["parameters"][name] = {
                    "value": d["value"],
                    "unit": d["unit"],
                    "verified": d["verified"],
                }
                if d.get("error"):
                    entry["parameters"][name]["error"] = d["error"]
            if r.get("error"):
                entry["error"] = r["error"]
            out.append(entry)
        if json_collect is not None:
            # Multi-PID --json: contribute one tagged block to the shared array.
            json_collect.append({"ecu": ecu_key, "pid": pid_key, "captures": out})
            return 0
        json.dump(out, sys.stdout, indent=2, default=str)
        print()
        return 0

    # Param column order (definition order; --try candidates appended last).
    param_names = list(parameters.keys())

    # Header
    n_verified = sum(1 for p in parameters.values() if p.get("verified", False))
    n_total = len(parameters)
    try_note = (
        f", {_CYAN}{len(candidate_names)} candidate (--try){_RESET}" if candidate_names else ""
    )
    print(
        f"\n{_BOLD}{ecu_key} PID {pid_key}{_RESET} — "
        f"{n_total} parameters ({n_verified} verified, {n_total - n_verified} unverified){try_note}, "
        f"{len(captures)} captures\n"
    )
    banner = scope_banner(since, until, args.state, args.label, args.first, args.last)
    if banner:
        print(f"  {_DIM}scope: {banner}{_RESET}\n")

    if scope_is_keep_unique(captures) and args.corr_transform in ("delta", "cumsum"):
        print(
            f"  {_YELLOW}⚠ --corr-transform {args.corr_transform} on keep:unique data is "
            f"unreliable — the time gaps between stored rows are dedup artifacts, not "
            f"real sampling intervals.{_RESET}"
        )
        print()
    if scope_is_keep_changes(captures):
        print(f"  {_YELLOW}⚠ {CHANGES_BANNER}.{_RESET}")
        if args.corr_transform in ("delta", "cumsum"):
            print(
                f"  {_YELLOW}  ⚠ --corr-transform {args.corr_transform} on keep:changes data is "
                f"unreliable — stored rows are value-transitions, not fixed-rate samples.{_RESET}"
            )
        print()

    if args.compact:
        print_compact(
            all_results, param_names, parameters, candidate_names, changes_only=args.changes_only
        )
        return 0

    # Default view: parameter value ranges across all captures (validation-focused).
    # --stats and --corr add/replace it with statistics and correlation tables.
    _notation = resolve_notation(args.notation)
    _sub_bytes = subfunction_bytes_for_pid(pid_key)
    printed = False
    if args.find_mirrors:
        print_mirrors(all_results, bits=args.bits, notation=_notation, sub_bytes=_sub_bytes)
        printed = True
    if args.stats:
        if args.group_by == "state":
            print_stats_grouped(all_results, param_names, parameters, candidate_names, "state")
        else:
            print_stats_table(all_results, param_names, parameters, candidate_names)
        printed = True
    if args.discriminate:
        disc_field = args.discriminate
        group_of = None
        if ":" in args.discriminate:
            try:
                axis_keys, disc_field = axis_group_keys(
                    all_results,
                    args.discriminate,
                    scope=scope,
                    tol_s=args.join_tol if args.join_tol is not None else DEFAULT_JOIN_TOL_S,
                )
            except ValueError as e:
                print(f"--discriminate error: {e}", file=sys.stderr)
                return 1
            group_of = lambda r: axis_keys.get(id(r))  # noqa: E731
        elif args.discriminate != "state":
            print(
                f"--discriminate: unknown axis {args.discriminate!r} — use 'state' or a "
                "cross-signal ECU:PID:PARAM",
                file=sys.stderr,
            )
            return 1
        print_discriminate(
            all_results,
            param_names,
            parameters,
            candidate_names,
            disc_field,
            include_bytes=args.bytes,
            include_bits=args.bits,
            notation=_notation,
            sub_bytes=_sub_bytes,
            group_of=group_of,
        )
        printed = True
    if corr_ref:
        print_correlations(
            all_results,
            param_names,
            parameters,
            candidate_names,
            corr_ref,
            cross_ref_series=cross_ref_series,
            cross_ref_label=cross_ref_label,
            tol_s=args.join_tol,
            transform=args.corr_transform,
            method=args.method,
        )
        printed = True
    if not printed:
        print_value_ranges(all_results, param_names, parameters, candidate_names)
        if sys.stdout.isatty():
            print(
                f"\n  {_DIM}Tip: add --plot to interactively explore these signals "
                f"(byte interpretations, transforms, correlations).{_RESET}"
            )
    return 0
