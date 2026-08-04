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

from canlib.align import (
    TimePoint,
    join_prepared,
    load_signal_captures,
    mirror_aligned_count,
    payload_lengths,
    prepare_series,
    series_time_ranges_disjoint,
)
from canlib.capture_dates import entry_datetime
from canlib.notation import ByteNotation, relabel_signal
from canlib.xanalysis import build_bit_series, build_byte_series

_BOLD = "\033[1m"
_DIM = "\033[2m"
_GREEN = "\033[92m"
_YELLOW = "\033[93m"
_RESET = "\033[0m"


def _color_r(r: float) -> str:
    c = _GREEN if abs(r) >= 0.7 else _YELLOW if abs(r) >= 0.3 else _DIM
    return f"{c}r={r:+.3f}{_RESET}"


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


def _print_cross_mirrors(
    specs, since, until, state, label, tol, min_n, bits, as_json, notation=ByteNotation.WICAN
) -> int:
    """Report byte/bit positions equal across co-polled ECU/PIDs (time-aligned).

    The cross-ECU companion to ``decode --find-mirrors`` (single-PID). Builds a
    varying byte (and, with ``bits``, bit) series per ECU:PID, time-aligns every
    cross-PID pair by nearest timestamp, and reports pairs equal in *all* aligned
    samples — a status bit an ECU exposes that another mirrors (e.g. an IGPM door
    bit also present in BCM). Same-PID pairs are excluded (that's decode's job).
    """
    loaded = load_signal_captures(specs, since=since, until=until, state=state, label=label)
    # Per-PID payload lengths: one mirror list mixes labels from several PIDs, so
    # each needs its own frame layout to render a correct non-WiCAN label.
    plens = payload_lengths(loaded)
    series: dict[str, list[TimePoint]] = {}
    for lp in loaded.values():
        if not lp.captures:
            continue
        series.update(build_byte_series(lp, min_distinct=2))
        if bits:
            series.update(build_bit_series(lp))

    def pid_of(sig_label: str) -> tuple[str, str]:
        ecu, pid, *_ = sig_label.split(":")
        return ecu, pid

    names = sorted(series)
    prepared = {name: prepare_series(series[name]) for name in names}
    mirrors: list[tuple[str, str, int]] = []
    for i in range(len(names)):
        a = names[i]
        pa = prepared[a]
        pid_a = pid_of(a)
        for j in range(i + 1, len(names)):
            b = names[j]
            if pid_a == pid_of(b):
                continue  # same PID — that's `decode --find-mirrors`
            pb = prepared[b]
            if series_time_ranges_disjoint(pa, pb, tol):
                continue
            n = mirror_aligned_count(pa, pb, tol_s=tol)
            if n >= min_n:
                mirrors.append((a, b, n))
    mirrors.sort(key=lambda t: -t[2])

    if as_json:
        _json.dump(
            {
                "join_tol_s": tol,
                "bits": bits,
                "mirrors": [{"a": a, "b": b, "n": n} for a, b, n in mirrors],
            },
            sys.stdout,
            indent=2,
            default=str,
        )
        print()
        return 0

    print(
        f"\n  {_BOLD}Cross-ECU mirrors{_RESET} "
        f"{_DIM}(time-aligned equal across PIDs, ≤{tol:g}s join, n≥{min_n}){_RESET}"
    )
    if not mirrors:
        print(
            f"    {_DIM}no cross-PID byte/bit position is equal across all aligned samples{_RESET}\n"
        )
        return 0
    for a, b, n in mirrors:
        print(
            f"    {_GREEN}n={n:<4}{_RESET} "
            f"{relabel_signal(a, notation, payload_lens=plens)}  "
            f"{_DIM}=={_RESET}  {relabel_signal(b, notation, payload_lens=plens)}"
        )
    print()
    return 0


def _print_can_mirrors(series: dict, path, args) -> int:
    """Report frame-byte/bit positions time-aligned equal ACROSS arbitration IDs.

    The domain-B analogue of the uds ``--find-mirrors``: a signal broadcast on two
    arbitration IDs (e.g. wheel speed on 0x386 and 0x331) shows up as two ``0xID:rN``
    series equal in every aligned sample. Same-ID pairs are skipped (intra-frame
    equality is not a mirror across messages).
    """

    def id_of(label: str) -> str:
        return label.split(":", 1)[0]

    # Pairwise time-join is the correct semantics (mirrors sampled at slightly
    # different times must still align within tolerance — a shared-timeline bucket
    # key is too strict and misses them). Two speedups keep it tractable:
    #   1. prepare each series once (epoch-seconds float arrays, pre-sorted) so the
    #      inner join avoids per-call sorts and datetime arithmetic;
    #   2. prune by sample-count overlap: a pair can't reach min_n aligned points
    #      if either series has fewer than min_n samples;
    #   3. a fused join+compare (mirror_aligned_count) that bails at the first value
    #      mismatch — most non-mirror pairs differ on an early sample.
    names = [k for k in sorted(series) if len(series[k]) >= args.min_n]
    prepared = {name: prepare_series(series[name]) for name in names}

    mirrors: list[tuple[str, str, int]] = []
    for i in range(len(names)):
        a = names[i]
        for j in range(i + 1, len(names)):
            b = names[j]
            if id_of(a) == id_of(b):
                continue  # same arbitration ID — not a cross-message mirror
            n = mirror_aligned_count(prepared[a], prepared[b], tol_s=args.join_tol)
            if n >= args.min_n:
                mirrors.append((a, b, n))
    mirrors.sort(key=lambda t: -t[2])
    mirrors = mirrors[: args.top]

    if args.json:
        _json.dump(
            {
                "can_log": path.name,
                "join_tol_s": args.join_tol,
                "bits": args.bits,
                "mirrors": [{"a": a, "b": b, "n": n} for a, b, n in mirrors],
            },
            sys.stdout,
            indent=2,
        )
        print()
        return 0

    print(
        f"\n  {_BOLD}Cross-ID frame mirrors{_RESET} "
        f"{_DIM}({path.name}, time-aligned equal across IDs, "
        f"≤{args.join_tol:g}s join, n≥{args.min_n}){_RESET}"
    )
    if not mirrors:
        print(
            f"    {_DIM}no cross-ID byte/bit position is equal across all aligned samples{_RESET}\n"
        )
        return 0
    for a, b, n in mirrors:
        print(f"    {_GREEN}n={n:<4}{_RESET} {a}  {_DIM}=={_RESET}  {b}")
    print()
    return 0
