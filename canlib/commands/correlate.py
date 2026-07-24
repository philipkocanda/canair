#!/usr/bin/env python3
"""``canair correlate`` — cross-signal correlation across a drive/session.

Builds every decoded param + every varying raw byte across all co-polled
ECU/PIDs in scope, time-aligns them by nearest timestamp, and ranks the
strongest cross-signal relationships. The "show me every strong relationship in
this drive" entry point — how the AAF-speed and MCU-temp finds were made by hand.

Read-only analysis over ``captures/``; talks to no device.
"""

from __future__ import annotations

import argparse
import json as _json
import sys

from canlib.align import (
    DEFAULT_JOIN_TOL_S,
    TimePoint,
    align_many,
    join_nearest,
    load_signal_captures,
)
from canlib.capture_dates import add_scope_args, entry_datetime, resolve_date_bounds
from canlib.xanalysis import (
    build_bit_series,
    build_byte_series,
    build_param_series,
    correlate_matrix,
    correlation,
    lag_scan,
    load_ref,
    transform_ref,
)

NAME = "correlate"

_BOLD = "\033[1m"
_DIM = "\033[2m"
_GREEN = "\033[92m"
_YELLOW = "\033[93m"
_RESET = "\033[0m"


def add_parser(subparsers) -> argparse.ArgumentParser:
    parser = subparsers.add_parser(
        NAME,
        help="Find every strong cross-signal relationship in a drive/session",
        description=(
            "Show me every strong relationship across a whole drive.\n\n"
            "Builds every decoded parameter (and, with --bytes, every varying raw\n"
            "byte; --bits for toggling bits) across all co-polled ECU/PIDs in scope,\n"
            "time-aligns them by nearest timestamp, and ranks the strongest\n"
            "cross-signal correlations. This is how the AAF-speed and MCU-temp links\n"
            "were originally found by hand.\n\n"
            "Three ways to use it:\n"
            "  (default)     ranked list of the strongest cross-ECU/PID pairs\n"
            "  --against R   rank every signal against one reference R=ECU:PID:PARAM\n"
            "  --matrix      a labelled correlation r-matrix\n\n"
            "Use --overlap first to see which ECU:PID pairs actually share aligned\n"
            "samples (so you pick a viable --against). --gate isolates a regime\n"
            "(e.g. 'while moving'), --lag-scan reveals command->response ordering,\n"
            "and --promote writes the top raw-byte hit into ecus/.\n\n"
            "Read-only: analyses captures/ only, never talks to the device. To pin\n"
            "down *which byte* a relationship lives in, follow up with `canair hunt`."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""\
examples:
  # every strong relationship in the most recent drive
  canair correlate --state driving

  # which ECU:PID pairs even share aligned samples? (pick an --against target)
  canair correlate --overlap --state driving

  # rank every signal against a known speed reference
  canair correlate --against ESC:22C101:REAL_SPEED_KMH --state driving

  # include raw bytes + bits (finds undecoded status/relay signals)
  canair correlate --against ESC:22C101:REAL_SPEED_KMH --bytes --bits

  # only while moving (isolate a regime whole-history correlation dilutes)
  canair correlate --against ESC:22C101:REAL_SPEED_KMH --gate '> 0'

  # restrict to a couple of ECUs and show the full r-matrix
  canair correlate "MCU VCU" --matrix

  # spearman ranks catch monotone-but-nonlinear links
  canair correlate --against ESC:22C101:REAL_SPEED_KMH --method spearman""",
    )
    parser.add_argument(
        "query",
        nargs="?",
        help="Optional ECU[:PID] selector(s) to restrict the signals "
        "(e.g. 'MCU VCU' or 'ESC:22C101'); default = all co-polled in scope",
    )
    parser.add_argument(
        "--against",
        metavar="ECU:PID:PARAM",
        help="Correlate every signal against this one reference "
        "(e.g. ESC:22C101:REAL_SPEED_KMH) instead of the full matrix",
    )
    parser.add_argument(
        "--transform",
        choices=["raw", "delta", "abs", "cumsum", "normalize", "smooth"],
        default="raw",
        metavar="MODE",
        help="With --against: transform the reference before aligning (e.g. "
        "delta to rank signals against the reference's *rate*)",
    )
    parser.add_argument(
        "--matrix",
        action="store_true",
        help="Print a labelled r-matrix instead of a ranked pair list",
    )
    parser.add_argument(
        "--include-intra",
        action="store_true",
        help="Include same-ECU+PID pairs (default: cross-PID/ECU only)",
    )
    parser.add_argument(
        "--include-self",
        action="store_true",
        help="With --against: keep the reference's own signal (trivial r=1.0; dropped by default)",
    )
    parser.add_argument(
        "--min-r", type=float, default=0.6, metavar="R", help="Min |r| to report (default 0.6)"
    )
    parser.add_argument(
        "--min-n", type=int, default=15, metavar="N", help="Min aligned points (default 15)"
    )
    parser.add_argument("--top", type=int, default=40, metavar="N", help="Max hits (default 40)")
    parser.add_argument(
        "--no-cluster",
        action="store_true",
        help="Don't collapse near-perfectly-correlated (|r|≥0.995) signal groups "
        "into a single summary line (e.g. balanced cell voltages while charging)",
    )
    parser.add_argument(
        "--method",
        choices=["pearson", "spearman"],
        default="pearson",
        help="Correlation coefficient: pearson (linear, default) or spearman "
        "(rank — catches monotone-but-nonlinear/quantized/saturating links)",
    )
    parser.add_argument(
        "--join-tol",
        type=float,
        default=DEFAULT_JOIN_TOL_S,
        metavar="SECONDS",
        help=f"Nearest-timestamp join window (default {DEFAULT_JOIN_TOL_S}s)",
    )
    parser.add_argument("--bytes", action="store_true", help="Include raw varying bytes (Bn)")
    parser.add_argument(
        "--bits",
        action="store_true",
        help="Include individual toggling bits (Bn:k) — point-biserial vs analog "
        "signals, φ vs other bits (cross-ECU); finds body-status/relay bits",
    )
    parser.add_argument(
        "--lag-scan",
        type=int,
        default=0,
        metavar="N",
        help="With --against: shift each signal by ±N sample-intervals and report "
        "the lag maximising |r| (apparent lag incl. poll offset — not proven "
        "causality). Reveals command→response ordering across ECUs",
    )
    parser.add_argument(
        "--gate",
        metavar="'[SIGNAL] OP VALUE'",
        help="With --against: only count points where a predicate holds, e.g. "
        "'> 0' (reference itself — 'while moving') or 'MCU:2102:MCU_MOTOR_RPM > 0' "
        "(a named signal). Isolates a regime whole-history correlation dilutes",
    )
    parser.add_argument(
        "--promote",
        metavar="NAME",
        help="With --against: write the top raw-byte hit to ecus/ as an enabled, "
        "unverified candidate param NAME (via pids upsert-param), with the "
        "correlation evidence auto-filled into notes",
    )
    parser.add_argument("--json", action="store_true", help="Machine-readable output")
    parser.add_argument(
        "--overlap",
        action="store_true",
        help="Instead of correlating, report which ECU:PID pairs share "
        "time-aligned samples (and how many) in scope — pick a viable "
        "--against reference without trial and error",
    )
    add_scope_args(parser)
    parser.set_defaults(func=run)
    return parser


def _discover_specs(query, since, until, state, label):
    """Which (ECU, PID) pairs have timed payload captures in scope."""
    from canlib.capture_dates import (
        entry_datetime,
        filter_by_date_range,
        filter_by_text,
    )
    from canlib.commands.captures import load_all_captures

    entries = load_all_captures()
    entries = filter_by_date_range(entries, since, until)
    entries = filter_by_text(entries, state=state, label=label)
    q = None
    if query:
        from canlib.query import parse_query

        q = parse_query(query)
    specs: set[tuple[str, str]] = set()
    for e in entries:
        ecu = str(e.get("ecu", "")).upper()
        pid = str(e.get("pid", "")).upper()
        if not e.get("payload") or entry_datetime(e) is None:
            continue
        if q is not None and not q.matches(ecu, pid):
            continue
        specs.add((ecu, pid))
    return sorted(specs)


def _gather_series(specs, since, until, state, label, want_bytes, want_bits=False):
    """Build all signal series (params + optionally varying bytes/bits) for specs."""
    from canlib.pids import build_ecu_index, load_pids

    loaded = load_signal_captures(specs, since=since, until=until, state=state, label=label)
    ecu_index = build_ecu_index(load_pids())
    series: dict = {}
    for (ecu, pid), lp in loaded.items():
        if not lp.captures:
            continue
        params = ecu_index.get(ecu, {}).get("pids", {}).get(pid, {}).get("parameters", {})
        series.update(build_param_series(lp, params))
        if want_bytes:
            series.update(build_byte_series(lp))
        if want_bits:
            series.update(build_bit_series(lp))
    return series


def _print_overlap(specs, since, until, state, label, tol, min_n, as_json) -> int:
    """Report which ECU:PID pairs share time-aligned samples (and how many).

    The "which reference can I actually use here?" index — answers the repeated
    "no timed captures for reference in scope" surprise before choosing an
    ``--against`` anchor. Overlap ``n`` is the nearest-join count within ``tol``.
    """
    loaded = load_signal_captures(specs, since=since, until=until, state=state, label=label)
    stamps: dict[str, list] = {}
    for (ecu, pid), lp in loaded.items():
        ts = [TimePoint(dt, 0.0) for c in lp.captures if (dt := entry_datetime(c)) is not None]
        if ts:
            stamps[f"{ecu}:{pid}"] = sorted(ts, key=lambda tp: tp.dt)

    names = sorted(stamps)
    pairs = []
    for i in range(len(names)):
        for j in range(i + 1, len(names)):
            _, _, n = join_nearest(stamps[names[i]], stamps[names[j]], tol_s=tol)
            if n:
                pairs.append((names[i], names[j], n))
    pairs.sort(key=lambda t: -t[2])

    if as_json:
        _json.dump(
            {
                "join_tol_s": tol,
                "signals": {k: len(v) for k, v in stamps.items()},
                "overlaps": [{"a": a, "b": b, "n": n} for a, b, n in pairs],
            },
            sys.stdout,
            indent=2,
            default=str,
        )
        print()
        return 0

    if not stamps:
        print("No timed captures in scope.", file=sys.stderr)
        return 1
    print(f"\n  {_BOLD}Co-poll overlap{_RESET} {_DIM}(nearest-join ≤{tol:g}s){_RESET}")
    for name in names:
        print(f"    {_DIM}{name}: {len(stamps[name])} timed samples{_RESET}")
    print()
    shown = [p for p in pairs if p[2] >= min_n]
    if not shown:
        print(f"    {_DIM}no pair shares ≥{min_n} aligned samples{_RESET}")
    for a, b, n in shown:
        color = _GREEN if n >= 50 else _YELLOW if n >= min_n else _DIM
        print(f"    {color}n={n:<4}{_RESET} {a}  {_DIM}⟷{_RESET}  {b}")
    print()
    return 0


def _color_r(r: float) -> str:
    c = _GREEN if abs(r) >= 0.7 else _YELLOW if abs(r) >= 0.3 else _DIM
    return f"{c}r={r:+.3f}{_RESET}"


_CLUSTER_THRESHOLD = 0.995


def _colinear_clusters(hits, threshold: float = _CLUSTER_THRESHOLD):
    """Union-find signals joined by ``|r| >= threshold`` into co-linear groups.

    Returns the list of clusters (sets of signal labels) with ≥3 members — the
    near-perfectly-correlated bundles (e.g. every balanced cell voltage during
    charging) that otherwise flood the ranked pair list with redundant rows.
    """
    parent: dict[str, str] = {}

    def find(x: str) -> str:
        parent.setdefault(x, x)
        root = x
        while parent[root] != root:
            root = parent[root]
        while parent[x] != root:
            parent[x], x = root, parent[x]
        return root

    for h in hits:
        if abs(h.r) >= threshold:
            ra, rb = find(h.a), find(h.b)
            if ra != rb:
                parent[ra] = rb
    groups: dict[str, set] = {}
    for sig in parent:
        groups.setdefault(find(sig), set()).add(sig)
    return [g for g in groups.values() if len(g) >= 3]


_GATE_OPS = {
    ">=": lambda a, b: a >= b,
    "<=": lambda a, b: a <= b,
    "!=": lambda a, b: a != b,
    "==": lambda a, b: a == b,
    ">": lambda a, b: a > b,
    "<": lambda a, b: a < b,
}


def _parse_gate(expr: str):
    """Parse a gate ``'[SIGNAL] OP VALUE'`` → ``(signal_or_None, op_fn, value, label)``.

    ``SIGNAL`` (empty ⇒ the reference itself) is a cross-signal ``ECU:PID:PARAM``.
    Raises ``ValueError`` on a malformed gate.
    """
    import re

    m = re.match(r"^\s*(.*?)\s*(>=|<=|==|!=|>|<)\s*(-?\d+(?:\.\d+)?)\s*$", expr)
    if not m:
        raise ValueError(
            f"invalid --gate {expr!r} (expected '[SIGNAL] OP VALUE', e.g. '> 0' or "
            "'MCU:2102:MCU_MOTOR_RPM > 0')"
        )
    signal = m.group(1).strip() or None
    return signal, _GATE_OPS[m.group(2)], float(m.group(3)), expr.strip()


def _apply_gate(ref_series, gate_expr, tol, *, since, until, state, label):
    """Filter ``ref_series`` to the points where the gate predicate holds.

    An omitted signal gates on the reference's own value; a named
    ``ECU:PID:PARAM`` signal is loaded and aligned onto the reference by nearest
    timestamp. Returns the filtered (time-sorted) reference series.
    """
    signal, op_fn, value, _lbl = _parse_gate(gate_expr)
    if signal is None:
        return [tp for tp in ref_series if op_fn(tp.value, value)]
    gate_series, _ = load_ref(signal, since=since, until=until, state=state, label=label)
    _, cols = align_many(ref_series, {"g": gate_series}, tol_s=tol)
    ref_sorted = sorted(ref_series, key=lambda tp: tp.dt)
    return [
        tp for tp, g in zip(ref_sorted, cols["g"], strict=True) if g is not None and op_fn(g, value)
    ]


def run(args) -> int:
    since, until, err = resolve_date_bounds(args)
    if err:
        print(f"error: {err}", file=sys.stderr)
        return 2

    specs = _discover_specs(args.query, since, until, args.state, args.label)

    if args.overlap:
        return _print_overlap(
            specs, since, until, args.state, args.label, args.join_tol, args.min_n, args.json
        )

    series = _gather_series(
        specs,
        since,
        until,
        args.state,
        args.label,
        args.bytes,
        args.bits,
    )
    if not series:
        print("No time-aligned signals found in scope.", file=sys.stderr)
        return 1

    # --against: rank every signal vs one reference.
    if args.against:
        try:
            ref_series, ref_label = load_ref(
                args.against, since=since, until=until, state=args.state, label=args.label
            )
        except ValueError as e:
            print(f"--against error: {e}", file=sys.stderr)
            return 1
        if args.transform and args.transform != "raw":
            ref_series = transform_ref(ref_series, args.transform)
            ref_label = f"{args.transform}({ref_label})"
        if args.gate:
            try:
                ref_series = _apply_gate(
                    ref_series,
                    args.gate,
                    args.join_tol,
                    since=since,
                    until=until,
                    state=args.state,
                    label=args.label,
                )
            except ValueError as e:
                print(f"--gate error: {e}", file=sys.stderr)
                return 1
            ref_label = f"{ref_label} [gate: {args.gate.strip()}]"
            if not ref_series:
                print(
                    f"--gate '{args.gate.strip()}' left no reference points in scope.",
                    file=sys.stderr,
                )
                return 1
        rows = []
        for name, s in series.items():
            if not args.include_self and name == args.against:
                continue  # the reference vs itself — trivial r=1.0
            if args.lag_scan:
                hit = lag_scan(
                    ref_series, s, tol_s=args.join_tol, max_lag=args.lag_scan, method=args.method
                )
                if hit is None or abs(hit.r) < args.min_r or hit.n < args.min_n:
                    continue
                rows.append((name, hit.r, hit.n, hit.lag_seconds))
            else:
                xs, ys, n = join_nearest(ref_series, s, tol_s=args.join_tol)
                if n < args.min_n:
                    continue
                r = correlation(xs, ys, args.method)
                if r is None or abs(r) < args.min_r:
                    continue
                rows.append((name, r, n, None))
        rows.sort(key=lambda t: -abs(t[1]))
        if args.promote:
            return _promote_top_byte(
                args.promote,
                [(n, r, nn) for n, r, nn, _ in rows],
                series,
                ref_series,
                ref_label,
                args.join_tol,
            )
        rows = rows[: args.top]
        if args.json:
            _json.dump(
                {
                    "reference": ref_label,
                    "method": args.method,
                    "join_tol_s": args.join_tol,
                    "lag_scan": args.lag_scan,
                    "hits": [
                        {"signal": n, "r": r, "n": nn, "lag_seconds": lag} for n, r, nn, lag in rows
                    ],
                },
                sys.stdout,
                indent=2,
            )
            print()
            return 0
        lag_hdr = (
            f", lag ±{args.lag_scan} samples (apparent, incl. poll offset)" if args.lag_scan else ""
        )
        print(
            f"\n  {_BOLD}vs {ref_label}{_RESET} "
            f"{_DIM}(nearest-join ≤{args.join_tol:g}s, ref {len(ref_series)} samples{lag_hdr}){_RESET}"
        )
        for name, r, n, lag in rows:
            lag_str = f"  {_DIM}lag={lag:+.1f}s{_RESET}" if lag is not None else ""
            print(f"    {_color_r(r)}  {name}  {_DIM}n={n}{_RESET}{lag_str}")
        print()
        return 0

    hits = correlate_matrix(
        series,
        tol_s=args.join_tol,
        min_r=args.min_r,
        min_n=args.min_n,
        include_intra=args.include_intra,
        method=args.method,
    )
    if args.json:
        _json.dump(
            {
                "join_tol_s": args.join_tol,
                "hits": [{"a": h.a, "b": h.b, "r": h.r, "n": h.n} for h in hits[: args.top]],
            },
            sys.stdout,
            indent=2,
        )
        print()
        return 0

    if not hits:
        print(f"No cross-signal correlations with |r| ≥ {args.min_r} (n ≥ {args.min_n}).")
        return 0

    clusters = [] if args.no_cluster else _colinear_clusters(hits)
    clustered = {sig for c in clusters for sig in c}
    # Pairs fully inside a collapsed cluster are hidden (represented by the
    # cluster summary); everything else prints normally.
    remaining = [
        h
        for h in hits
        if not (
            h.a in clustered and h.b in clustered and any(h.a in c and h.b in c for c in clusters)
        )
    ]
    remaining = remaining[: args.top]
    print(
        f"\n  {_BOLD}Cross-signal correlations{_RESET} "
        f"{_DIM}({len(series)} signals, |r|≥{args.min_r}, n≥{args.min_n}, "
        f"≤{args.join_tol:g}s){_RESET}"
    )
    for c in sorted(clusters, key=len, reverse=True):
        members = sorted(c)
        shown = ", ".join(members[:4]) + (f", +{len(members) - 4} more" if len(members) > 4 else "")
        print(
            f"    {_GREEN}≈ cluster{_RESET} {_DIM}(|r|≥{_CLUSTER_THRESHOLD:g}, "
            f"{len(members)} signals){_RESET}  {shown}"
        )
    for h in remaining:
        print(f"    {_color_r(h.r)}  {h.a}  {_DIM}⟷{_RESET}  {h.b}  {_DIM}n={h.n}{_RESET}")
    print()
    return 0


def _promote_top_byte(name, rows, series, ref_series, ref_label, tol) -> int:
    """Promote the strongest raw-byte hit vs the reference to a candidate param.

    Only raw bytes (``Bn``) are promotable — an already-defined param needs no
    promotion. Routes through the shared guarded write, with a fresh linear fit
    and unit guess added to the evidence notes.
    """
    import re

    from canlib.commands._promote import print_promoted, write_candidate
    from canlib.pids_edit import PidsEditError
    from canlib.xanalysis import linear_fit, sniff_unit

    byte_hit = None
    for sig, r, n in rows:
        parts = sig.split(":")
        if len(parts) == 3 and re.fullmatch(r"B\d+", parts[2]):
            byte_hit = (sig, parts[0], parts[1], parts[2], r, n)
            break
    if byte_hit is None:
        print(
            "Nothing to promote — no raw-byte hit in the ranked list. "
            "Re-run with --bytes so undecoded bytes are considered.",
            file=sys.stderr,
        )
        return 1

    sig, ecu, pid, expr, r, n = byte_hit
    xs, ys, _ = join_nearest(ref_series, series[sig], tol_s=tol)
    fit = linear_fit(xs, ys)
    fit_note = f", fit y={fit[0]:.4f}·x{fit[1]:+.2f}, resid={fit[2]:.2f}" if fit else ""
    unit = sniff_unit(xs, ys)
    unit_note = f" {unit}" if unit else ""
    notes = (
        f"Candidate from `canair correlate --against {ref_label}`: r={r:+.3f} (n={n})"
        f"{fit_note}.{unit_note} Enabled unverified — confirm scale/sign against reality."
    )
    try:
        fpath = write_candidate(
            ecu, pid, name, expr, source=f"canair correlate vs {ref_label}", notes=notes
        )
    except (PidsEditError, SystemExit) as e:
        print(f"promote failed: {e}", file=sys.stderr)
        return 1
    print_promoted(ecu, pid, name, expr, r, fpath)
    return 0
