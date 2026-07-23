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

from canlib.align import DEFAULT_JOIN_TOL_S, align_many, join_nearest, load_signal_captures
from canlib.capture_dates import add_scope_args, resolve_date_bounds
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
        help="Cross-signal correlation matrix across co-polled ECUs in a drive",
        description=(
            "Time-align every decoded param + varying raw byte across the "
            "co-polled ECU/PIDs in scope and rank the strongest cross-signal "
            "correlations. Use --against to hunt one reference (ECU:PID:PARAM)."
        ),
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
        "--min-r", type=float, default=0.6, metavar="R", help="Min |r| to report (default 0.6)"
    )
    parser.add_argument(
        "--min-n", type=int, default=15, metavar="N", help="Min aligned points (default 15)"
    )
    parser.add_argument("--top", type=int, default=40, metavar="N", help="Max hits (default 40)")
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

    loaded = load_signal_captures(
        specs, since=since, until=until, state=state, label=label
    )
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


def _color_r(r: float) -> str:
    c = _GREEN if abs(r) >= 0.7 else _YELLOW if abs(r) >= 0.3 else _DIM
    return f"{c}r={r:+.3f}{_RESET}"


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
        tp
        for tp, g in zip(ref_sorted, cols["g"], strict=True)
        if g is not None and op_fn(g, value)
    ]


def run(args) -> int:
    since, until, err = resolve_date_bounds(args)
    if err:
        print(f"error: {err}", file=sys.stderr)
        return 2

    series = _gather_series(
        _discover_specs(args.query, since, until, args.state, args.label),
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
                    ref_series, args.gate, args.join_tol,
                    since=since, until=until, state=args.state, label=args.label,
                )
            except ValueError as e:
                print(f"--gate error: {e}", file=sys.stderr)
                return 1
            ref_label = f"{ref_label} [gate: {args.gate.strip()}]"
            if not ref_series:
                print(f"--gate '{args.gate.strip()}' left no reference points in scope.",
                      file=sys.stderr)
                return 1
        rows = []
        for name, s in series.items():
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
                args.promote, [(n, r, nn) for n, r, nn, _ in rows], series,
                ref_series, ref_label, args.join_tol
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
                        {"signal": n, "r": r, "n": nn, "lag_seconds": lag}
                        for n, r, nn, lag in rows
                    ],
                },
                sys.stdout,
                indent=2,
            )
            print()
            return 0
        lag_hdr = (
            f", lag ±{args.lag_scan} samples (apparent, incl. poll offset)"
            if args.lag_scan else ""
        )
        print(
            f"\n  {_BOLD}vs {ref_label}{_RESET} "
            f"{_DIM}(nearest-join ≤{args.join_tol:g}s{lag_hdr}){_RESET}"
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
    hits = hits[: args.top]
    if args.json:
        _json.dump(
            {
                "join_tol_s": args.join_tol,
                "hits": [{"a": h.a, "b": h.b, "r": h.r, "n": h.n} for h in hits],
            },
            sys.stdout,
            indent=2,
        )
        print()
        return 0

    if not hits:
        print(f"No cross-signal correlations with |r| ≥ {args.min_r} (n ≥ {args.min_n}).")
        return 0
    print(
        f"\n  {_BOLD}Cross-signal correlations{_RESET} "
        f"{_DIM}({len(series)} signals, |r|≥{args.min_r}, n≥{args.min_n}, "
        f"≤{args.join_tol:g}s){_RESET}"
    )
    for h in hits:
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
