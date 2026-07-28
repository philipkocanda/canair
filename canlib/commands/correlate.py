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
    join_nearest,
    join_prepared,
    load_signal_captures,
    prepare_series,
)
from canlib.capture_dates import add_scope_args, entry_datetime, resolve_scope_bounds
from canlib.commands._can_args import add_can_log_source_args
from canlib.commands._correlate_calc import _apply_gate
from canlib.commands._correlate_render import (
    _color_r,
    _print_can_mirrors,
    _print_cross_mirrors,
    _print_overlap,
)
from canlib.commands._group import group_help
from canlib.keepmode import BANNER as KEEP_BANNER
from canlib.keepmode import scope_is_keep_unique
from canlib.notation import add_notation_arg, relabel_signal, resolve_notation
from canlib.stats import METHOD_CHEAT_SHEET as _METHOD_CHEAT_SHEET
from canlib.xanalysis import _CLUSTER_THRESHOLD as _CLUSTER_THRESHOLD
from canlib.xanalysis import (
    build_bit_series,
    build_byte_series,
    build_param_series,
    colinear_clusters,
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
        help="Find every strong cross-signal relationship: uds (captures) | can (frame log)",
        description=(
            "Find every strong cross-signal relationship across a drive/session.\n"
            "Choose a domain kind:\n"
            "  uds   diagnostic captures (default) — co-polled ECU/PID params/bytes\n"
            "        (domain A). A bare `canair correlate …` is shorthand for this.\n"
            "  can   a raw broadcast-CAN frame log's per-byte series (domain B),\n"
            "        bytes labelled 0xID:rN.\n\n"
            "Read-only: analyses captures/ only, never talks to the device. To pin\n"
            "down *which byte* a relationship lives in, follow up with `canair hunt`."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    kinds = parser.add_subparsers(dest="correlate_kind", metavar="<kind>")
    _add_uds_parser(kinds)
    _add_can_parser(kinds)
    parser.set_defaults(func=group_help("_correlate_group_parser"), _correlate_group_parser=parser)
    return parser


def _add_can_parser(kinds) -> argparse.ArgumentParser:
    parser = kinds.add_parser(
        "can",
        help="Correlate a raw broadcast-CAN frame log's per-byte series (domain B)",
        description=(
            "Correlate the per-byte series of a raw broadcast-CAN frame log "
            "(.asc/.blf/candump .log/.trc/GVRET .csv) — bytes are labelled 0xID:rN. "
            "--against/--bits/--id/--min-r/--top/--find-mirrors all apply."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=_METHOD_CHEAT_SHEET,
    )
    add_can_log_source_args(parser)
    parser.add_argument(
        "--id",
        metavar="IDS",
        help="Restrict to comma-separated arbitration IDs (e.g. 0x220,0x386)",
    )
    parser.add_argument(
        "--include-intra",
        action="store_true",
        help="Include same-arbitration-ID pairs (default: cross-ID only)",
    )
    parser.add_argument(
        "--find-mirrors",
        action="store_true",
        help="Instead of ranking correlations, report byte/bit positions that are "
        "time-aligned equal ACROSS arbitration IDs — a signal mirrored on two IDs "
        "(e.g. wheel speed on 0x386 and 0x331). Use with --bits for bit-level",
    )
    parser.add_argument(
        "--no-cluster",
        action="store_true",
        help="Don't collapse near-perfectly-correlated (|r|≥0.995) byte groups into one line",
    )
    _add_shared_analysis_args(parser)
    add_notation_arg(parser)
    parser.set_defaults(func=_run_can_log)
    return parser


def _add_shared_analysis_args(parser) -> None:
    """Flags common to both the uds and can correlate kinds."""
    parser.add_argument(
        "--against",
        metavar="ECU:PID:PARAM",
        help="Correlate every signal against this one reference "
        "(e.g. ESC:22C101:REAL_SPEED_KMH) instead of the full matrix",
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
        choices=["pearson", "spearman", "cramers_v", "mutual_info"],
        default="pearson",
        help="Association coefficient: pearson (linear, default) or spearman "
        "(rank — catches monotone-but-nonlinear/quantized/saturating links), or "
        "the categorical cramers_v / mutual_info (treat each value as a nominal "
        "category — for mode/flag/enum bytes where numeric spacing is meaningless)",
    )
    parser.add_argument(
        "--join-tol",
        type=float,
        default=DEFAULT_JOIN_TOL_S,
        metavar="SECONDS",
        help=f"Nearest-timestamp join window (default {DEFAULT_JOIN_TOL_S}s)",
    )
    parser.add_argument(
        "--bits",
        action="store_true",
        help="Include individual toggling bits (rN:k / Bn:k)",
    )
    parser.add_argument("--json", action="store_true", help="Machine-readable output")


def _add_uds_parser(kinds) -> argparse.ArgumentParser:
    parser = kinds.add_parser(
        "uds",
        help="Correlate co-polled diagnostic captures (domain A)",
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
        epilog=f"""\
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

  # rank every byte against an EXTERNAL log (meter/GPS/grid), nearest-timestamp join
  canair correlate --against-file grid_voltage.csv --bytes --state charging

  # partial correlation: rank every byte vs the grid with the charge current
  # regressed out — surfaces a signal only visible once the driver is removed
  canair correlate --against-file grid_voltage.csv --control OBC:2101:OBC_DC_A --bytes

  # spearman ranks catch monotone-but-nonlinear links
  canair correlate --against ESC:22C101:REAL_SPEED_KMH --method spearman

{_METHOD_CHEAT_SHEET}""",
    )
    parser.add_argument(
        "query",
        nargs="?",
        help="Optional ECU[:PID] selector(s) to restrict the signals "
        "(e.g. 'MCU VCU' or 'ESC:22C101'); default = all co-polled in scope",
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
        "--against-file",
        dest="against_file",
        metavar="FILE",
        help="Rank every signal against an external CSV (timestamp,value) reference "
        "instead of a bus signal — a calibrated meter log, GPS track, grid-voltage "
        "export. Joined by nearest timestamp; the file must be on the same absolute "
        "clock as the captures (relative/zero-based logs won't align)",
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
    _add_shared_analysis_args(parser)
    parser.add_argument(
        "--no-cluster",
        action="store_true",
        help="Don't collapse near-perfectly-correlated (|r|≥0.995) signal groups "
        "into a single summary line (e.g. balanced cell voltages while charging)",
    )
    parser.add_argument("--bytes", action="store_true", help="Include raw varying bytes (Bn)")
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
        "--control",
        metavar="ECU:PID:PARAM",
        help="With --against: regress out this nuisance signal and rank by the "
        "PARTIAL correlation (what remains after removing the control's linear "
        "influence) — surfaces signals visible only once the dominant driver is "
        "removed. --control-file takes an external timestamp,value CSV instead",
    )
    parser.add_argument(
        "--control-file",
        dest="control_file",
        metavar="FILE",
        help="Like --control, but the nuisance signal is an external "
        "timestamp,value CSV (mutually exclusive with --control)",
    )
    parser.add_argument(
        "--promote",
        metavar="NAME",
        help="With --against: write the top raw-byte hit to ecus/ as an enabled, "
        "unverified candidate param NAME (via pids upsert-param), with the "
        "correlation evidence auto-filled into notes",
    )
    parser.add_argument(
        "--overlap",
        action="store_true",
        help="Instead of correlating, report which ECU:PID pairs share "
        "time-aligned samples (and how many) in scope — pick a viable "
        "--against reference without trial and error",
    )
    parser.add_argument(
        "--find-mirrors",
        action="store_true",
        help="Instead of ranking correlations, report byte/bit positions that are "
        "time-aligned equal ACROSS co-polled ECU/PIDs — e.g. a door bit in IGPM "
        "mirrored in BCM. Use with --bits for bit-level. Cross-ECU companion to "
        "`decode --find-mirrors` (which is single-PID)",
    )
    add_notation_arg(parser)
    add_scope_args(parser)
    parser.set_defaults(func=run)
    return parser


def _discover_specs(query, since, until, state, label):
    """Which (ECU, PID) pairs have timed payload captures in scope."""
    from canlib.capture_dates import (
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


def _scope_keep_unique(specs, since, until, state, label) -> bool:
    """True if any capture in scope came from a keep:unique session."""
    loaded = load_signal_captures(specs, since=since, until=until, state=state, label=label)
    return any(scope_is_keep_unique(lp.captures) for lp in loaded.values())


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


def _run_can_log(args) -> int:
    """Correlate a raw broadcast-CAN frame log's per-byte (and --bits) series.

    Domain B: reads the native frame log into ``0xID:rN`` series and runs the
    *same* correlate core as the diagnostic path — ranked cross-ID pairs by
    default, every byte vs an ``--against 0xID:rN`` reference, or (with
    ``--find-mirrors``) byte/bit positions time-aligned equal across IDs. Series
    sharing an arbitration ID are intra (dropped unless --include-intra), so
    cross-ID relationships surface first.
    """
    from pathlib import Path

    from canlib import frame_series
    from canlib.can_logs import CanLogError

    path = Path(args.file)
    if not path.is_file():
        print(f"correlate can: no such file: {path}", file=sys.stderr)
        return 1
    try:
        id_filter = frame_series.parse_id_filter(args.id)
        if args.bits:
            series = frame_series.build_frame_bit_series(path, args.can_format, id_filter=id_filter)
        else:
            series = frame_series.build_frame_series(
                path, args.can_format, id_filter=id_filter, min_distinct=4
            )
    except (ValueError, CanLogError) as e:
        print(f"correlate can: {e}", file=sys.stderr)
        return 1
    if not series:
        print("No varying frame bytes found in scope.", file=sys.stderr)
        return 1

    src = f"{path.name} ({len(series)} varying {'bits' if args.bits else 'bytes'})"

    if args.find_mirrors:
        return _print_can_mirrors(series, path, args)

    # --against: rank every series vs one reference label present in the log.
    if args.against:
        ref = series.get(args.against)
        if ref is None:
            avail = ", ".join(sorted(series)[:6])
            print(
                f"--against: {args.against!r} is not a varying byte in {path.name}. "
                f"Available e.g.: {avail} …",
                file=sys.stderr,
            )
            return 1
        rows = []
        ref_prepared = prepare_series(ref)
        for name, s in series.items():
            if name == args.against:
                continue
            xs, ys, n = join_prepared(ref_prepared, prepare_series(s), tol_s=args.join_tol)
            if n < args.min_n:
                continue
            r = correlation(xs, ys, args.method)
            if r is None or abs(r) < args.min_r:
                continue
            rows.append((name, r, n))
        rows.sort(key=lambda t: -abs(t[1]))
        rows = rows[: args.top]
        if args.json:
            _json.dump(
                {
                    "can_log": path.name,
                    "against": args.against,
                    "hits": [{"signal": n, "r": r, "n": nn} for n, r, nn in rows],
                },
                sys.stdout,
                indent=2,
            )
            print()
            return 0
        print(
            f"\n  {_BOLD}vs {args.against}{_RESET} "
            f"{_DIM}({src}, nearest-join ≤{args.join_tol:g}s){_RESET}"
        )
        if not rows:
            print(f"    {_DIM}no byte with |r| ≥ {args.min_r} (n ≥ {args.min_n}){_RESET}\n")
            return 0
        for name, r, n in rows:
            print(f"    {_color_r(r)}  {name}  {_DIM}n={n}{_RESET}")
        print()
        return 0

    # Default: ranked cross-ID pairs.
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
                "can_log": path.name,
                "hits": [{"a": h.a, "b": h.b, "r": h.r, "n": h.n} for h in hits[: args.top]],
            },
            sys.stdout,
            indent=2,
        )
        print()
        return 0
    if not hits:
        print(f"No cross-ID frame-byte correlations with |r| ≥ {args.min_r} (n ≥ {args.min_n}).")
        return 0
    clusters = [] if args.no_cluster else colinear_clusters(hits)
    clustered = {sig for c in clusters for sig in c}
    remaining = [
        h
        for h in hits
        if not (
            h.a in clustered and h.b in clustered and any(h.a in c and h.b in c for c in clusters)
        )
    ][: args.top]
    print(
        f"\n  {_BOLD}Frame-byte correlations{_RESET} "
        f"{_DIM}({src}, |r|≥{args.min_r}, n≥{args.min_n}){_RESET}"
    )
    for c in sorted(clusters, key=len, reverse=True):
        members = sorted(c)
        shown = ", ".join(members[:4]) + (f", +{len(members) - 4} more" if len(members) > 4 else "")
        print(f"    {_GREEN}≈ cluster{_RESET} {_DIM}({len(members)} signals){_RESET}  {shown}")
    for h in remaining:
        print(f"    {_color_r(h.r)}  {h.a}  {_DIM}⟷{_RESET}  {h.b}  {_DIM}n={h.n}{_RESET}")
    print()
    return 0


def run(args) -> int:
    since, until, err = resolve_scope_bounds(args)
    if err:
        print(f"error: {err}", file=sys.stderr)
        return 2

    if args.against and args.against_file:
        print("error: --against and --against-file are mutually exclusive", file=sys.stderr)
        return 2

    notation = resolve_notation(args.notation)
    specs = _discover_specs(args.query, since, until, args.state, args.label)

    if args.overlap:
        return _print_overlap(
            specs, since, until, args.state, args.label, args.join_tol, args.min_n, args.json
        )

    if args.find_mirrors:
        return _print_cross_mirrors(
            specs,
            since,
            until,
            args.state,
            args.label,
            args.join_tol,
            args.min_n,
            args.bits,
            args.json,
            notation,
        )

    keep_unique = _scope_keep_unique(specs, since, until, args.state, args.label)
    if keep_unique and not args.json:
        print(f"  {_YELLOW}⚠ {KEEP_BANNER}.{_RESET}")
        if args.transform in ("delta", "cumsum") or args.lag_scan:
            what = (
                f"--transform {args.transform}"
                if args.transform in ("delta", "cumsum")
                else "--lag-scan"
            )
            print(
                f"  {_YELLOW}  ⚠ {what} on keep:unique data is unreliable — stored-row time "
                f"gaps are dedup artifacts, not real sampling intervals.{_RESET}"
            )
        print()

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

    # --against / --against-file: rank every signal vs one reference.
    if args.against or args.against_file:
        try:
            if args.against_file:
                from canlib.align import load_reference_file

                ref_series, ref_label = load_reference_file(args.against_file)
            else:
                ref_series, ref_label = load_ref(
                    args.against, since=since, until=until, state=args.state, label=args.label
                )
        except ValueError as e:
            flag = "--against-file" if args.against_file else "--against"
            print(f"{flag} error: {e}", file=sys.stderr)
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

        # --control: rank by partial correlation with a nuisance signal removed.
        control_series = None
        if args.control and args.control_file:
            print("error: --control and --control-file are mutually exclusive", file=sys.stderr)
            return 2
        if args.control or args.control_file:
            if args.method in ("cramers_v", "mutual_info"):
                print(
                    "error: --control (partial correlation) is undefined for a "
                    "categorical --method (cramers_v/mutual_info)",
                    file=sys.stderr,
                )
                return 2
            if args.lag_scan:
                print("error: --control cannot be combined with --lag-scan", file=sys.stderr)
                return 2
            try:
                if args.control_file:
                    from canlib.align import load_reference_file

                    control_series, control_label = load_reference_file(args.control_file)
                else:
                    control_series, control_label = load_ref(
                        args.control, since=since, until=until, state=args.state, label=args.label
                    )
            except ValueError as e:
                flag = "--control-file" if args.control_file else "--control"
                print(f"{flag} error: {e}", file=sys.stderr)
                return 1
            ref_label = f"{ref_label} · control {control_label}"

        rows = []
        ref_prepared = prepare_series(ref_series)
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
            elif control_series is not None:
                from canlib.align import join_nearest_triple
                from canlib.stats import partial_correlation

                xs, ys, zs, n = join_nearest_triple(
                    ref_series, s, control_series, tol_s=args.join_tol
                )
                if n < args.min_n:
                    continue
                r = partial_correlation(xs, ys, zs, args.method)
                if r is None or abs(r) < args.min_r:
                    continue
                rows.append((name, r, n, None))
            else:
                xs, ys, n = join_prepared(ref_prepared, prepare_series(s), tol_s=args.join_tol)
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
            print(
                f"    {_color_r(r)}  {relabel_signal(name, notation)}  {_DIM}n={n}{_RESET}{lag_str}"
            )
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

    clusters = [] if args.no_cluster else colinear_clusters(hits)
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
        shown_members = [relabel_signal(m, notation) for m in members[:4]]
        shown = ", ".join(shown_members) + (
            f", +{len(members) - 4} more" if len(members) > 4 else ""
        )
        print(
            f"    {_GREEN}≈ cluster{_RESET} {_DIM}(|r|≥{_CLUSTER_THRESHOLD:g}, "
            f"{len(members)} signals){_RESET}  {shown}"
        )
    for h in remaining:
        print(
            f"    {_color_r(h.r)}  {relabel_signal(h.a, notation)}  {_DIM}⟷{_RESET}  "
            f"{relabel_signal(h.b, notation)}  {_DIM}n={h.n}{_RESET}"
        )
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
