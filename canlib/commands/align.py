#!/usr/bin/env python3
"""``canair align`` — a time-aligned, wide table of several cross-ECU signals.

The other analysis commands (``correlate``/``hunt``/``decode --corr``) *consume* a
nearest-timestamp cross-signal join internally but only emit correlation
summaries; ``captures``/``decode`` show one PID at a time. ``align`` fills the
gap: given several ``ECU:PID:PARAM`` selectors it emits one **row per reference
sample** with a column per signal, nearest-joined within a tolerance — the
"show me A, B, C side by side over this window" tool for eyeballing a regime,
exporting a drive slice, or feeding an external plot/``--against-file``.

The **first** selector sets the row cadence (each of its samples is a row); the
rest are joined onto it by nearest timestamp. Pure analysis over ``captures/`` —
nothing talks to the device. This is the ``uds`` (diagnostic) domain; a ``can``
(raw broadcast frame) counterpart is future work.
"""

from __future__ import annotations

import argparse
import csv
import json
import sys

from canlib.align import (
    DEFAULT_JOIN_TOL_S,
    SignalRef,
    align_many,
    extract_series,
    load_signal_captures,
)
from canlib.capture_dates import add_scope_args, resolve_scope_bounds
from canlib.keepmode import CHANGES_BANNER, scope_is_keep_changes

NAME = "align"

_BOLD = "\033[1m"
_DIM = "\033[2m"
_CYAN = "\033[96m"
_YELLOW = "\033[93m"
_RESET = "\033[0m"


def add_parser(subparsers) -> argparse.ArgumentParser:
    parser = subparsers.add_parser(
        NAME,
        help="Time-aligned wide table of several cross-ECU signals (CSV/JSON/table)",
        description=(
            "Emit a time-aligned, wide table of several ECU:PID:PARAM signals — one row "
            "per reference sample, one column per signal, nearest-joined within a tolerance. "
            "The first selector sets the row cadence; the rest join onto it."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "Examples:\n"
            "  # eyeball the compressor state, heat power and pack power together\n"
            '  canair align "HVAC:220102:HVAC_COMPRESSOR_ON HVAC:2201A2:HVAC_HEAT_POWER '
            'BMS:2101:BATTERY_POWER" --state charging\n\n'
            "  # export a drive slice to CSV for an external tool / --against-file\n"
            "  canair align ESC:22C101:REAL_SPEED_KMH MCU:2102:[S10:S11] --state driving --csv > drive.csv\n\n"
            "  # machine-readable rows\n"
            "  canair align BMS:2101:BATTERY_POWER VCU:2102:VCU_AUX_POWER --json\n"
        ),
    )
    parser.add_argument(
        "selectors",
        nargs="+",
        metavar="ECU:PID:PARAM",
        help="Two or more signal selectors (ECU:PID:PARAM or ECU:PID:EXPR). May be "
        "separate args or one quoted whitespace-separated string. The first is the "
        "reference (sets the row cadence).",
    )
    fmt = parser.add_mutually_exclusive_group()
    fmt.add_argument("--csv", action="store_true", help="Output CSV (time + one column per signal)")
    fmt.add_argument("--json", action="store_true", help="Output JSON (list of row objects)")
    parser.add_argument(
        "--join-tol",
        type=float,
        default=DEFAULT_JOIN_TOL_S,
        metavar="SECONDS",
        help=f"Nearest-join tolerance in seconds (default {DEFAULT_JOIN_TOL_S})",
    )
    add_scope_args(parser)
    parser.set_defaults(func=run)
    return parser


def _load_one(spec: str, *, since, until, state, label):
    """Load one ``ECU:PID:PARAM|EXPR`` signal → ``(label, series, captures)``.

    Mirrors ``_decode_calc.load_cross_ref_series`` but also returns the backing
    captures so the caller can flag ``keep:changes`` scope. Raises ``ValueError``
    with a clean message when the signal can't be built.
    """
    from canlib.pids import build_ecu_index, load_pids

    sref = SignalRef.parse(spec)
    loaded = load_signal_captures(
        [(sref.ecu, sref.pid)], since=since, until=until, state=state, label=label
    )
    lp = loaded[(sref.ecu.upper(), sref.pid.upper())]
    if not lp.captures:
        raise ValueError(
            f"no timed captures for {sref.ecu}:{sref.pid} in scope"
            + (f" ({lp.n_no_time} untimed skipped)" if lp.n_no_time else "")
        )
    ecu_pids = build_ecu_index(load_pids()).get(sref.ecu.upper(), {}).get("pids", {})
    params = ecu_pids.get(sref.pid.upper(), {}).get("parameters", {})
    series = extract_series(lp, sref.name_or_expr, parameters=params)
    if not series:
        raise ValueError(f"{sref.label} decoded no numeric values in scope")
    return sref.label, series, lp.captures


def run(args) -> int:
    since, until, err = resolve_scope_bounds(args)
    if err:
        print(err, file=sys.stderr)
        return 2

    # Accept both `align A:B:C D:E:F` and `align "A:B:C D:E:F"`.
    tokens: list[str] = []
    for tok in args.selectors:
        tokens.extend(tok.split())
    if len(tokens) < 2:
        print(
            "align: need at least two signals to align (the first is the reference)",
            file=sys.stderr,
        )
        return 2

    labels: list[str] = []
    series_by_label: dict[str, list] = {}
    any_keep_changes = False
    for tok in tokens:
        try:
            label, series, caps = _load_one(
                tok, since=since, until=until, state=args.state, label=args.label
            )
        except ValueError as e:
            print(f"align: {e}", file=sys.stderr)
            return 1
        if label in series_by_label:
            print(f"align: duplicate signal {label!r} ignored", file=sys.stderr)
            continue
        labels.append(label)
        series_by_label[label] = series
        any_keep_changes = any_keep_changes or scope_is_keep_changes(caps)

    if len(labels) < 2:
        print("align: need at least two distinct signals", file=sys.stderr)
        return 2

    ref_label = labels[0]
    ref_series = series_by_label[ref_label]
    others = {lbl: series_by_label[lbl] for lbl in labels[1:]}
    ref_sorted = sorted(ref_series, key=lambda tp: tp.dt)
    _ref_vals, cols = align_many(ref_series, others, tol_s=args.join_tol)

    _warn_thin_joins(cols, labels, len(ref_sorted), args.join_tol)

    rows: list[tuple] = []  # (datetime, {label: value|None})
    for i, tp in enumerate(ref_sorted):
        values: dict[str, float | None] = {ref_label: tp.value}
        for lbl in labels[1:]:
            values[lbl] = cols[lbl][i]
        rows.append((tp.dt, values))

    if args.json:
        _emit_json(rows, labels)
    elif args.csv:
        _emit_csv(rows, labels)
    else:
        _emit_table(rows, labels, args.join_tol, any_keep_changes)
    return 0


def _warn_thin_joins(cols, labels, n_ref: int, tol: float) -> None:
    """Warn (stderr) when a joined signal landed on few/no reference rows.

    A silent all-empty column reads like a broken tool; the usual cause is a
    ``--join-tol`` too tight for the inter-ECU skew of a round-robin poll. Fires
    for any non-reference signal that joined 0 rows, or fewer than 5%.
    """
    if n_ref == 0:
        return
    floor = max(1, n_ref // 20)  # 5%
    for lbl in labels[1:]:
        n_joined = sum(1 for v in cols[lbl] if v is not None)
        if n_joined == 0:
            print(
                f"align: warning: {lbl!r} joined 0 of {n_ref} reference rows "
                f"(within \u2264{tol:g}s) \u2014 widen --join-tol or check the scope overlaps",
                file=sys.stderr,
            )
        elif n_joined < floor:
            print(
                f"align: warning: {lbl!r} joined only {n_joined} of {n_ref} reference rows "
                f"(within \u2264{tol:g}s) \u2014 consider a larger --join-tol",
                file=sys.stderr,
            )


def _emit_json(rows, labels) -> None:
    out = [
        {
            "date": dt.date().isoformat(),
            "time": dt.strftime("%H:%M:%S.%f"),
            "values": {lbl: values[lbl] for lbl in labels},
        }
        for dt, values in rows
    ]
    print(json.dumps(out, indent=2))


def _emit_csv(rows, labels) -> None:
    w = csv.writer(sys.stdout)
    w.writerow(["time", *labels])
    for dt, values in rows:
        cells = ["" if values[lbl] is None else values[lbl] for lbl in labels]
        w.writerow([dt.strftime("%Y-%m-%d %H:%M:%S.%f"), *cells])


def _fmt_val(v: float | None) -> str:
    if v is None:
        return "—"
    if v == int(v):
        return str(int(v))
    return f"{v:.3f}".rstrip("0").rstrip(".")


def _emit_table(rows, labels, tol: float, keep_changes: bool = False) -> None:
    # Short cN handles keep the table narrow; a legend maps them to full labels.
    handles = [f"c{i + 1}" for i in range(len(labels))]
    print(
        f"\n  {_BOLD}align{_RESET} — {len(labels)} signals, {len(rows)} rows "
        f"{_DIM}(nearest-join ≤{tol}s; c1 is the reference){_RESET}"
    )
    if keep_changes:
        print(f"  {_YELLOW}⚠ {CHANGES_BANNER}{_RESET}")
    for h, lbl in zip(handles, labels, strict=True):
        print(f"  {_DIM}{h} = {lbl}{_RESET}")

    str_rows = [
        [dt.strftime("%H:%M:%S"), *[_fmt_val(values[lbl]) for lbl in labels]] for dt, values in rows
    ]
    headers = ["time", *handles]
    widths = [
        max(len(headers[c]), *(len(r[c]) for r in str_rows)) if str_rows else len(headers[c])
        for c in range(len(headers))
    ]
    print("  " + "  ".join(h.rjust(widths[i]) for i, h in enumerate(headers)))
    for r in str_rows:
        print("  " + "  ".join(cell.rjust(widths[i]) for i, cell in enumerate(r)))
    print()
