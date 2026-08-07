#!/usr/bin/env python3
"""Presentation layer for ``canair correlate`` (extracted from correlate.py).

The self-contained sub-report renderers: the co-poll overlap index
(:func:`_print_overlap`), the cross-ECU (:func:`_print_cross_mirrors`) and
cross-ID (:func:`_print_can_mirrors`) mirror reports, and the ``r``-value colorizer
(:func:`_color_r`). Each loads/computes its own slice and prints (or emits JSON);
keeping them here leaves correlate.py as argparse + the ranked-pair/`--against`
orchestration.
"""

from __future__ import annotations

import json as _json
import sys

from canlib import ansi
from canlib.align import (
    TimePoint,
    join_prepared,
    load_signal_captures,
    payload_lengths,
    prepare_series,
    series_time_ranges_disjoint,
)
from canlib.capture_dates import entry_datetime
from canlib.notation import ByteNotation, relabel_signal
from canlib.xanalysis import build_bit_series, build_byte_series, build_param_series


def _color_r(r: float) -> str:
    c = ansi.GREEN if abs(r) >= 0.7 else ansi.YELLOW if abs(r) >= 0.3 else ansi.DIM
    return f"{c}r={r:+.3f}{ansi.RESET}"


def weak_correlation_hint(best_r: float | None, best_label: str | None, min_r: float) -> str | None:
    """Retry hint naming the strongest sub-threshold correlation, or ``None``.

    When the ``--min-r`` floor rejects everything, name the ``|r|`` that WOULD
    have surfaced a result (computed from the data) and the concrete ``--min-r``
    to re-run with, so the empty report is a lead rather than a dead end. The
    suggested floor is rounded *down* so the re-run actually includes the hit.
    Consistent phrasing across correlate's ranked and ``--against`` empty paths.
    """
    import math

    if best_r is None or best_label is None:
        return None
    suggested = math.floor(abs(best_r) * 100) / 100
    return (
        f"Nothing at |r| ≥ {min_r:g}. Strongest below it: {best_label} at "
        f"|r|={abs(best_r):.3f}. Re-run with --min-r {suggested:g} to see it "
        "— treat it as a lead, not a finding."
    )


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
    prepared = {name: prepare_series(stamps[name]) for name in names}
    pairs = []
    for i in range(len(names)):
        pa = prepared[names[i]]
        for j in range(i + 1, len(names)):
            pb = prepared[names[j]]
            if series_time_ranges_disjoint(pa, pb, tol):
                continue
            _, _, n = join_prepared(pa, pb, tol_s=tol)
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
    print(
        f"\n  {ansi.BOLD}Co-poll overlap{ansi.RESET} {ansi.DIM}(nearest-join ≤{tol:g}s){ansi.RESET}"
    )
    for name in names:
        print(f"    {ansi.DIM}{name}: {len(stamps[name])} timed samples{ansi.RESET}")
    print()
    shown = [p for p in pairs if p[2] >= min_n]
    if not shown:
        print(f"    {ansi.DIM}no pair shares ≥{min_n} aligned samples{ansi.RESET}")
    for a, b, n in shown:
        color = ansi.GREEN if n >= 50 else ansi.YELLOW if n >= min_n else ansi.DIM
        print(f"    {color}n={n:<4}{ansi.RESET} {a}  {ansi.DIM}⟷{ansi.RESET}  {b}")
    print()
    return 0


def _mirror_summary(hit) -> str:
    """How well a reported mirror held (row count, and the agreement that earned it)."""
    return f"{ansi.DIM}{hit.relation.quality()}{ansi.RESET}"


def _mirror_flags(args) -> dict:
    """The mirror-search knobs shared by the uds and can sweeps."""
    return {
        "min_fraction": args.mirror_match,
        "allow_offset": args.allow_offset,
    }


def _mirror_header(args) -> str:
    """The parenthetical describing what this sweep accepted as a mirror."""
    what = "equal" if not args.allow_offset else "equal up to a constant offset/scale"
    pct = f"in ≥{args.mirror_match * 100:.0f}% of aligned rows"
    return f"{what} {pct}, ≤{args.join_tol:g}s join, n≥{args.min_n}"


def _print_cross_mirrors(specs, since, until, args, notation=ByteNotation.WICAN) -> int:
    """Report byte/bit positions mirrored across co-polled ECU/PIDs (time-aligned).

    The cross-ECU companion to ``decode --find-mirrors`` (single-PID). Builds a
    varying byte (and, with ``--bits``, bit) series per ECU:PID, time-aligns every
    cross-PID pair by nearest timestamp, and reports pairs that agree on at least
    ``--mirror-match`` of their aligned rows — a status bit an ECU exposes that
    another mirrors (e.g. an IGPM door bit also present in BCM), or with
    ``--allow-offset`` the same physical quantity at a different offset/scale.
    Same-PID pairs are excluded (that's decode's job).
    """
    from canlib.commands._join import fill_policy_from_args
    from canlib.mirrors import find_series_mirrors
    from canlib.pids import build_ecu_index, load_pids

    fill = fill_policy_from_args(args)
    loaded = load_signal_captures(
        specs, since=since, until=until, state=args.state, label=args.label
    )
    # Per-PID payload lengths: one mirror list mixes labels from several PIDs, so
    # each needs its own frame layout to render a correct non-WiCAN label.
    plens = payload_lengths(loaded)
    ecu_index = build_ecu_index(load_pids())
    series: dict[str, list[TimePoint]] = {}
    for (ecu, pid), lp in loaded.items():
        if not lp.captures:
            continue
        # Defined params are included, not just raw bytes: the most valuable mirror
        # is an unknown byte on one ECU matching a *named, decoded* signal on
        # another (AAF:2181:B19 - 100 is the OBC's LDC temperature), which a
        # bytes-only sweep can never report.
        params = ecu_index.get(ecu, {}).get("pids", {}).get(pid, {}).get("parameters", {})
        series.update(build_param_series(lp, params, fill=fill))
        series.update(build_byte_series(lp, min_distinct=2, fill=fill))
        if args.bits:
            series.update(build_bit_series(lp, fill=fill))

    def same_pid(a: str, b: str) -> bool:
        return a.split(":")[:2] == b.split(":")[:2]

    prepared = {name: prepare_series(s) for name, s in series.items()}
    mirrors = find_series_mirrors(
        prepared,
        tol_s=args.join_tol,
        min_n=args.min_n,
        same_source=same_pid,
        **_mirror_flags(args),
    )

    if args.json:
        _json.dump(
            {
                "join_tol_s": args.join_tol,
                "bits": args.bits,
                "mirror_match": args.mirror_match,
                "allow_offset": args.allow_offset,
                "mirrors": [h.as_json() for h in mirrors],
            },
            sys.stdout,
            indent=2,
            default=str,
        )
        print()
        return 0

    print(
        f"\n  {ansi.BOLD}Cross-ECU mirrors{ansi.RESET} {ansi.DIM}({_mirror_header(args)}){ansi.RESET}"
    )
    if not mirrors:
        print(f"    {ansi.DIM}no cross-PID byte/bit position mirrors another{ansi.RESET}\n")
        return 0
    for hit in mirrors:
        la = relabel_signal(hit.a, notation, payload_lens=plens)
        lb = relabel_signal(hit.b, notation, payload_lens=plens)
        print(
            f"    {ansi.GREEN}{la}{ansi.RESET}  {ansi.DIM}=={ansi.RESET}  {hit.relation.describe(lb)}  "
            f"{_mirror_summary(hit)}"
        )
    print()
    return 0


def _print_can_mirrors(series: dict, path, args) -> int:
    """Report frame-byte/bit positions mirrored ACROSS arbitration IDs.

    The domain-B analogue of the uds ``--find-mirrors``: a signal broadcast on two
    arbitration IDs (e.g. wheel speed on 0x386 and 0x331) shows up as two ``0xID:rN``
    series that agree wherever they align. Same-ID pairs are skipped (intra-frame
    equality is not a mirror across messages).
    """
    from canlib.mirrors import find_series_mirrors

    def same_id(a: str, b: str) -> bool:
        return a.split(":", 1)[0] == b.split(":", 1)[0]

    prepared = {name: prepare_series(s) for name, s in series.items() if len(s) >= args.min_n}
    mirrors = find_series_mirrors(
        prepared,
        tol_s=args.join_tol,
        min_n=args.min_n,
        same_source=same_id,
        **_mirror_flags(args),
    )[: args.top]

    if args.json:
        _json.dump(
            {
                "can_log": path.name,
                "join_tol_s": args.join_tol,
                "bits": args.bits,
                "mirror_match": args.mirror_match,
                "allow_offset": args.allow_offset,
                "mirrors": [h.as_json() for h in mirrors],
            },
            sys.stdout,
            indent=2,
        )
        print()
        return 0

    print(
        f"\n  {ansi.BOLD}Cross-ID frame mirrors{ansi.RESET} "
        f"{ansi.DIM}({path.name}, {_mirror_header(args)}){ansi.RESET}"
    )
    if not mirrors:
        print(f"    {ansi.DIM}no cross-ID byte/bit position mirrors another{ansi.RESET}\n")
        return 0
    for hit in mirrors:
        print(
            f"    {ansi.GREEN}{hit.a}{ansi.RESET}  {ansi.DIM}=={ansi.RESET}  "
            f"{hit.relation.describe(hit.b)}  {_mirror_summary(hit)}"
        )
    print()
    return 0
