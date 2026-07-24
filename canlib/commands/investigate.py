#!/usr/bin/env python3
"""``canair investigate ECU PID`` — one-shot "tell me everything about this PID".

Bundles the manual reverse-engineering battery — coverage (mapped?),
state-discriminability, the best co-polled cross-signal anchor, and a physical
unit guess — into one ranked, per-byte report. The "point it at an unknown PID"
entry point that collapses a coverage → discriminate → correlate → hunt loop.

Read-only analysis over ``captures/``; talks to no device.
"""

from __future__ import annotations

import argparse
import json as _json
import sys
from dataclasses import dataclass

from canlib.align import DEFAULT_JOIN_TOL_S, join_nearest, load_signal_captures
from canlib.byteindex import mapped_bits, mapped_offsets
from canlib.capture_dates import add_scope_args, resolve_date_bounds
from canlib.keepmode import scope_is_keep_unique
from canlib.xanalysis import (
    build_bit_series,
    build_byte_series,
    build_param_series,
    correlation,
    linear_fit,
    sniff_unit,
)

NAME = "investigate"

_BOLD = "\033[1m"
_DIM = "\033[2m"
_GREEN = "\033[92m"
_YELLOW = "\033[93m"
_CYAN = "\033[96m"
_RESET = "\033[0m"


def add_parser(subparsers) -> argparse.ArgumentParser:
    parser = subparsers.add_parser(
        NAME,
        help="Explain an unknown PID: one ranked per-byte report (mapped? / state / anchor / unit)",
        description=(
            "Point this at an unknown PID and get one ranked table telling you\n"
            "everything worth knowing about each of its bytes — the fastest way to\n"
            "start decoding.\n\n"
            "For every varying data byte of ECU PID it reports, in one pass:\n"
            "  - mapped?   whether a defined parameter already decodes this byte\n"
            "              (a verified param hides the byte by default; an\n"
            "              unverified [param?] mapping is shown as still-open work)\n"
            "  - stateF    how cleanly the byte separates across power states\n"
            "              (sleep/acc/ready/charging) — high F = a mode/relay/thermal\n"
            "              signal a driving correlation would miss\n"
            "  - anchor    the strongest-correlating known signal on another\n"
            "              co-polled ECU/PID (Pearson r + linear fit y=m·x+c)\n"
            "  - unit      a physical-unit guess for that fit (e.g. raw-40 degC,\n"
            "              x1.609 mph->km/h)\n\n"
            "Bytes are ranked strongest-anchor-first, then by state separation, so\n"
            "the most decodable bytes float to the top. This bundles the manual\n"
            "coverage -> discriminate -> correlate -> hunt loop into a single call.\n\n"
            "Read-only: analyses captures/ only, never talks to the device. Once a\n"
            "byte looks promising, confirm the exact expression with `canair hunt\n"
            "ECU PID --against ...` and write it with `canair pids upsert-param`."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""\
examples:
  canair investigate MCU 2102              # rank unmapped + unverified-mapped bytes of MCU 2102
  canair investigate MCU 2102 --all        # include bytes a verified param already maps
  canair investigate IGPM 22BC03 --bits    # rank toggling bits (body/status-ECU work)
  canair investigate IGPM 22BC03 --events  # bit/byte edges aligned to the event timeline
  canair investigate BMS 2101 --state driving   # only consider drive captures
  canair investigate ESC 22C101 --min-r 0.8      # only show strong anchors (|r| >= 0.8)
  canair investigate AAF 2181 --json       # machine-readable output

tip: no anchors found? widen scope (drop --state), lower --min-r, or grow the
     capture set — an anchor needs another co-polled signal it can align to. For
     a body/comfort PID with no co-polled partner, use --bits / --events (the
     signals are toggling status bits, ranked by state separation + edge time).""",
    )
    parser.add_argument("ecu", help="Target ECU (e.g. MCU)")
    parser.add_argument("pid", help="Target PID (e.g. 2102)")
    parser.add_argument(
        "--min-r",
        type=float,
        default=0.6,
        metavar="R",
        help="Only report an anchor when |r| ≥ this (default 0.6)",
    )
    parser.add_argument(
        "--min-n", type=int, default=15, metavar="N", help="Min aligned points (default 15)"
    )
    parser.add_argument(
        "--join-tol",
        type=float,
        default=DEFAULT_JOIN_TOL_S,
        metavar="SECONDS",
        help=f"Nearest-timestamp join window (default {DEFAULT_JOIN_TOL_S}s)",
    )
    parser.add_argument(
        "--all",
        action="store_true",
        help="Include bytes a verified param already maps (default: hide only verified-mapped)",
    )
    parser.add_argument(
        "--bits",
        action="store_true",
        help="Also analyse individual toggling bits (Bn:k) — the body/status-ECU finder",
    )
    parser.add_argument(
        "--events",
        action="store_true",
        help="Report each bit/byte rising/falling edge with its timestamp, aligned to "
        "the nearest capture note (the narrated event timeline)",
    )
    parser.add_argument("--json", action="store_true", help="Machine-readable output")
    add_scope_args(parser)
    parser.set_defaults(func=run)
    return parser


@dataclass
class _ByteReport:
    offset: int
    mapped_by: str | None
    mapped_verified: bool
    state_f: float | None
    anchor: str | None
    anchor_r: float | None
    anchor_n: int
    slope: float | None
    intercept: float | None
    unit_guess: str | None
    bit: int | None = None  # None = whole byte; 0-7 = a single bit Bn:k

    @property
    def label(self) -> str:
        return f"B{self.offset}:{self.bit}" if self.bit is not None else f"B{self.offset}"


def _state_f(frames_by_state: dict[str, list[float]]):
    from canlib.commands.decode import _discriminability

    return _discriminability(frames_by_state)


def run(args) -> int:
    from canlib.commands.correlate import _discover_specs
    from canlib.commands.decode import _byte_state_buckets
    from canlib.ecus import canonical_ecu_name_safe
    from canlib.pids import build_ecu_index, load_pids

    since, until, err = resolve_date_bounds(args)
    if err:
        print(f"error: {err}", file=sys.stderr)
        return 2

    ecu = canonical_ecu_name_safe(args.ecu)
    pid = args.pid.upper()
    scope = {"since": since, "until": until, "state": args.state, "label": args.label}

    loaded = load_signal_captures([(ecu, pid)], **scope)
    lp = loaded[(ecu.upper(), pid)]
    if not lp.captures:
        print(
            f"No timed captures for {ecu} {pid} in scope"
            + (f" ({lp.n_no_time} untimed skipped)." if lp.n_no_time else "."),
            file=sys.stderr,
        )
        return 1

    # Target byte series (min_distinct=2 so near-binary relay bytes count).
    target = build_byte_series(lp, min_distinct=2)

    # Which offsets/bits are already mapped by a defined param, and at what confidence.
    ecu_index = build_ecu_index(load_pids())
    params_def = ecu_index.get(ecu.upper(), {}).get("pids", {}).get(pid, {}).get("parameters", {})
    mapped = mapped_offsets(params_def)
    mapped_bit = mapped_bits(params_def)

    # State buckets per byte/bit (F score) — reuse decode's bucketer over a lite
    # all_results (only needs r["capture"]).
    all_results = [{"capture": c} for c in lp.captures]
    state_buckets = _byte_state_buckets(all_results, "state", include_bits=args.bits)

    # --events short-circuits to the edge/timeline view (no anchor correlation).
    if args.events:
        _print_events(ecu, pid, lp, mapped, mapped_bit, args)
        return 0

    # Anchor signals: every param on the OTHER co-polled ECU/PIDs in scope.
    anchors: dict[str, list] = {}
    other_specs = [
        s
        for s in _discover_specs(None, since, until, args.state, args.label)
        if s != (ecu.upper(), pid)
    ]
    if other_specs:
        aloaded = load_signal_captures(other_specs, **scope)
        for (aecu, apid), alp in aloaded.items():
            if not alp.captures:
                continue
            aparams = ecu_index.get(aecu, {}).get("pids", {}).get(apid, {}).get("parameters", {})
            anchors.update(build_param_series(alp, aparams))

    reports: list[_ByteReport] = []
    for key, series in target.items():
        off = int(key.rsplit(":B", 1)[1])
        best = _best_anchor(series, anchors, args.join_tol, args.min_n)
        sb = state_buckets.get(f"B{off}")
        m = mapped.get(off)
        reports.append(
            _ByteReport(
                offset=off,
                mapped_by=m[0] if m else None,
                mapped_verified=m[1] if m else False,
                state_f=_state_f(sb) if sb else None,
                anchor=best[0] if best else None,
                anchor_r=best[1] if best else None,
                anchor_n=best[2] if best else 0,
                slope=best[3] if best else None,
                intercept=best[4] if best else None,
                unit_guess=best[5] if best else None,
            )
        )

    if args.bits:
        for key, series in build_bit_series(lp).items():
            off, bit = _parse_bit_key(key)
            best = _best_anchor(series, anchors, args.join_tol, args.min_n)
            sb = state_buckets.get(f"B{off}:{bit}")
            m = mapped_bit.get((off, bit))
            reports.append(
                _ByteReport(
                    offset=off,
                    mapped_by=m[0] if m else None,
                    mapped_verified=m[1] if m else False,
                    state_f=_state_f(sb) if sb else None,
                    anchor=best[0] if best else None,
                    anchor_r=best[1] if best else None,
                    anchor_n=best[2] if best else 0,
                    slope=best[3] if best else None,
                    intercept=best[4] if best else None,
                    unit_guess=best[5] if best else None,
                    bit=bit,
                )
            )

    if not args.all:
        # Hide only positions a *verified* param already decodes; unverified-mapped
        # positions are unfinished work, so surface them alongside unmapped ones.
        reports = [r for r in reports if not r.mapped_verified]
    # Rank: strongest anchor first, then state separation.
    reports.sort(key=lambda r: (-(abs(r.anchor_r or 0)), -(r.state_f or 0)))

    if args.json:
        _json.dump(
            {
                "target": f"{ecu}:{pid}",
                "join_tol_s": args.join_tol,
                "keep_unique": scope_is_keep_unique(lp.captures),
                "bytes": [vars(r) for r in reports],
            },
            sys.stdout,
            indent=2,
            default=str,
        )
        print()
        return 0

    _print_report(ecu, pid, reports, args, lp, bool(anchors))
    return 0


def _parse_bit_key(key: str) -> tuple[int, int]:
    """``ECU:PID:B10:5`` → ``(10, 5)`` — the last two colon fields are offset:bit."""
    _, off, bit = key.rsplit(":", 2)
    return int(off.lstrip("B")), int(bit)


def _best_anchor(series, anchors, tol, min_n):
    """The strongest-correlating anchor for one byte series → (label,r,n,m,c,unit)."""
    best = None
    for label, asig in anchors.items():
        xs, ys, n = join_nearest(asig, series, tol_s=tol)
        if n < min_n:
            continue
        r = correlation(xs, ys)
        if r is None:
            continue
        if best is None or abs(r) > abs(best[1]):
            fit = linear_fit(xs, ys)
            m, c = (fit[0], fit[1]) if fit else (None, None)
            best = (label, r, n, m, c, sniff_unit(xs, ys))
    return best


def _print_report(ecu, pid, reports, args, lp, has_anchors: bool) -> None:
    print(
        f"\n  {_BOLD}Investigate {ecu} {pid}{_RESET} "
        f"{_DIM}({len(lp.captures)} timed captures, ≤{args.join_tol:g}s join){_RESET}"
    )
    _print_keep_banner(lp.captures)
    if not reports:
        what = "varying " if not args.all else ""
        unit = "bytes/bits" if args.bits else "bytes"
        print(f"    {_DIM}no {what}{unit} to report{_RESET}\n")
        return
    for r in reports:
        if r.mapped_by is None:
            tag = f"{_YELLOW}unmapped{_RESET}"
        elif r.mapped_verified:
            tag = f"{_DIM}[{r.mapped_by}]{_RESET}"
        else:
            tag = f"{_YELLOW}[{r.mapped_by}?]{_RESET}"  # mapped but unverified — still open
        f_str = ""
        if r.state_f is not None:
            fc = _GREEN if r.state_f >= 10 else _YELLOW if r.state_f >= 2 else _DIM
            f_val = "∞" if r.state_f == float("inf") else f"{r.state_f:.1f}"
            f_str = f"  {fc}stateF={f_val}{_RESET}"
        anchor = ""
        if r.anchor and r.anchor_r is not None and abs(r.anchor_r) >= args.min_r:
            rc = _GREEN if abs(r.anchor_r) >= 0.7 else _YELLOW
            fit = f" fit y={r.slope:.4f}·x{r.intercept:+.2f}" if r.slope is not None else ""
            unit = f" {_CYAN}{r.unit_guess}{_RESET}" if r.unit_guess else ""
            anchor = f"  {rc}r={r.anchor_r:+.3f}{_RESET} vs {r.anchor} {_DIM}n={r.anchor_n}{fit}{_RESET}{unit}"
        print(f"    {_BOLD}{r.label}{_RESET} {tag}{f_str}{anchor}")
    if not has_anchors:
        # Body/comfort PIDs have no co-polled partner, so there is no anchor
        # column — that's expected, not "nothing here". Point at the right tool.
        print(
            f"    {_DIM}no co-polled anchor in scope — ranked by state separation. "
            f"For status bits try {_BOLD}--events{_RESET}{_DIM} (edge timeline).{_RESET}"
        )
    print()


def _print_keep_banner(captures) -> None:
    """Warn when the scope includes keep:unique sessions (rising-edge-only data)."""
    if scope_is_keep_unique(captures):
        print(
            f"    {_YELLOW}⚠ scope includes keep:unique sessions — only rising-edge "
            f"transitions were stored; falling edges/durations are absent.{_RESET}"
        )


def _iter_edges(lp, mapped, mapped_bit, *, bits: bool):
    """Yield (dt, label, before, after, mapped_by, verified) for every value change.

    Walks each varying byte (and, with ``bits``, each toggling bit) in time order
    and emits one row per transition — the raw material for the event timeline.
    """
    from canlib.byteindex import payload_to_wican_bytes, wican_to_isotp
    from canlib.capture_dates import entry_datetime

    frames = []
    max_len = 0
    for cap in lp.captures:
        dt = entry_datetime(cap)
        if dt is None:
            continue
        try:
            fr = payload_to_wican_bytes(cap["payload"])
        except Exception:
            continue
        frames.append((dt, fr, cap))
        max_len = max(max_len, len(fr))
    frames.sort(key=lambda t: t[0])

    edges = []
    for off in range(max_len):
        if wican_to_isotp(off) is None:
            continue
        prev_byte: int | None = None
        prev_bit: dict[int, int] = {}
        for dt, fr, cap in frames:
            if off >= len(fr):
                continue
            val = fr[off]
            bit_edges_here = []
            if bits:
                for k in range(8):
                    b = (val >> k) & 1
                    pb = prev_bit.get(k)
                    if pb is not None and b != pb:
                        mb = mapped_bit.get((off, k))
                        bit_edges_here.append(
                            (
                                dt,
                                f"B{off}:{k}",
                                pb,
                                b,
                                mb[0] if mb else None,
                                mb[1] if mb else False,
                                cap,
                                "bit",
                            )
                        )
                    prev_bit[k] = b
            if prev_byte is not None and val != prev_byte:
                if bit_edges_here:
                    # The bit rows carry the same information at finer resolution;
                    # keep them and drop the redundant whole-byte edge.
                    edges.extend(bit_edges_here)
                elif bits:
                    # --bits on but no isolated bit mapped/toggling here — show the
                    # byte edge unattributed (a byte may hold many params).
                    edges.append((dt, f"B{off}", prev_byte, val, None, False, cap, "byte"))
                else:
                    # Byte-only mode: attribute to the covering param if any.
                    m = mapped.get(off)
                    edges.append(
                        (
                            dt,
                            f"B{off}",
                            prev_byte,
                            val,
                            m[0] if m else None,
                            m[1] if m else False,
                            cap,
                            "byte",
                        )
                    )
            elif bit_edges_here:
                edges.extend(bit_edges_here)
            prev_byte = val
    edges.sort(key=lambda e: e[0])
    return edges


def _print_events(ecu, pid, lp, mapped, mapped_bit, args) -> None:
    """Edge/event-timeline view: each transition with its time and nearest note."""
    edges = _iter_edges(lp, mapped, mapped_bit, bits=args.bits)
    if args.json:
        _json.dump(
            {
                "target": f"{ecu}:{pid}",
                "keep_unique": scope_is_keep_unique(lp.captures),
                "events": [
                    {
                        "time": e[0].strftime("%H:%M:%S"),
                        "signal": e[1],
                        "before": e[2],
                        "after": e[3],
                        "mapped_by": e[4],
                        "verified": e[5],
                        "note": _cap_note(e[6]),
                    }
                    for e in edges
                ],
            },
            sys.stdout,
            indent=2,
            default=str,
        )
        print()
        return
    print(
        f"\n  {_BOLD}Events {ecu} {pid}{_RESET} {_DIM}({len(lp.captures)} timed captures){_RESET}"
    )
    _print_keep_banner(lp.captures)
    if not edges:
        print(f"    {_DIM}no transitions in scope.{_RESET}\n")
        return
    for dt, label, before, after, mapped_by, verified, cap, _kind in edges:
        if mapped_by is None:
            tag = f"{_YELLOW}candidate{_RESET}"
        elif verified:
            tag = f"{_DIM}[{mapped_by}]{_RESET}"
        else:
            tag = f"{_YELLOW}[{mapped_by}?]{_RESET}"
        note = _cap_note(cap)
        note_str = f"  {_DIM}~ note: {_CYAN}{note}{_RESET}" if note else ""
        arrow = (
            f"{_BOLD}{before:#04x}→{after:#04x}{_RESET}" if _kind == "byte" else f"{before}→{after}"
        )
        print(
            f"    {_DIM}{dt.strftime('%H:%M:%S')}{_RESET}  {_BOLD}{label}{_RESET} {arrow}  {tag}{note_str}"
        )
    print()


def _cap_note(cap) -> str:
    """The best free-text note for a capture: its own note, else the session's."""
    return str(cap.get("notes") or cap.get("session_notes") or "").strip()
