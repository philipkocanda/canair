#!/usr/bin/env python3
"""``canair hunt`` — which byte on ECU:PID *is* this known signal?

Sweeps every byte offset × interpretation (u8/i16/f32/… × endianness) on a
target PID, time-aligns each against a reference signal from another ECU/PID,
and ranks by |Pearson r| — reporting the best linear fit and a physical-unit
guess for each top hit. Automates the "which byte tracks vehicle speed?"
question that previously needed a scratch script.

Read-only analysis over ``captures/``; talks to no device.
"""

from __future__ import annotations

import argparse
import json as _json
import sys

from canlib.align import DEFAULT_JOIN_TOL_S, DEFAULT_SESSION_GAP_S, load_signal_captures
from canlib.capture_dates import add_scope_args, resolve_scope_bounds
from canlib.commands._can_args import add_can_log_source_args
from canlib.commands._group import group_help
from canlib.notation import (
    ByteNotation,
    ByteRef,
    add_notation_arg,
    resolve_notation,
    subfunction_bytes_for_pid,
)
from canlib.xanalysis import (
    hunt_byte,
    load_ref,
    ref_unit_for,
    reference_is_absolute_level,
    reference_is_bimodal,
    transform_ref,
)

NAME = "hunt"

_BOLD = "\033[1m"
_DIM = "\033[2m"
_GREEN = "\033[92m"
_YELLOW = "\033[93m"
_CYAN = "\033[96m"
_RESET = "\033[0m"


def add_parser(subparsers) -> argparse.ArgumentParser:
    parser = subparsers.add_parser(
        NAME,
        help="Identify which byte *is* a known signal: uds (a PID) | can (a frame ID)",
        description=(
            "Answer 'which byte carries a signal I already know?' Choose a domain kind:\n"
            "  uds   sweep a diagnostic PID's bytes vs a known signal (domain A).\n"
            "        A bare `canair hunt AAF 2181 --against …` is shorthand for this.\n"
            "  can   sweep a raw broadcast-CAN frame ID's bytes vs a reference frame\n"
            "        byte in the same log (domain B), bytes labelled 0xID:rN.\n\n"
            "Read-only: analyses captures/ only, never talks to the device."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    kinds = parser.add_subparsers(dest="hunt_kind", metavar="<kind>")
    _add_uds_parser(kinds)
    _add_can_parser(kinds)
    parser.set_defaults(func=group_help("_hunt_group_parser"), _hunt_group_parser=parser)
    return parser


def _add_shared_hunt_args(parser) -> None:
    """Flags common to both the uds and can hunt kinds."""
    parser.add_argument(
        "--min-n", type=int, default=10, metavar="N", help="Min aligned points (default 10)"
    )
    parser.add_argument("--top", type=int, default=12, metavar="N", help="Max hits (default 12)")
    parser.add_argument(
        "--transform",
        choices=["raw", "delta", "abs", "cumsum", "normalize", "smooth"],
        default="raw",
        metavar="MODE",
        help="Transform the reference before aligning (e.g. delta to hunt the "
        "byte that tracks the reference's *rate* — torque vs acceleration)",
    )
    parser.add_argument(
        "--method",
        choices=["pearson", "spearman"],
        default="pearson",
        help="Ranking coefficient: pearson (linear, default) or spearman (rank)",
    )
    parser.add_argument(
        "--join-tol",
        type=float,
        default=DEFAULT_JOIN_TOL_S,
        metavar="SECONDS",
        help=f"Nearest-timestamp join window (default {DEFAULT_JOIN_TOL_S}s)",
    )
    parser.add_argument("--json", action="store_true", help="Machine-readable output")
    parser.add_argument(
        "--all-interps",
        action="store_true",
        help="Show every interpretation per offset (u8/i16/u24/…); default "
        "collapses to the best interpretation per byte offset",
    )


def _add_can_parser(kinds) -> argparse.ArgumentParser:
    parser = kinds.add_parser(
        "can",
        help="Hunt a raw broadcast-CAN frame ID's bytes vs a reference (domain B)",
        description=(
            "Hunt on a raw broadcast-CAN frame log: sweep every byte/interpretation "
            "of --id's frames vs --against (a frame byte 0xID:rN in the same log). "
            "Hits are raw-CAN rN labels (no WiCAN expr); --promote is not supported "
            "for frames yet (frame signals are defined in signals/)."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    add_can_log_source_args(parser)
    parser.add_argument(
        "--id",
        required=True,
        metavar="ID",
        help="The arbitration ID to hunt on (e.g. 0x220)",
    )
    parser.add_argument(
        "--against",
        required=True,
        metavar="0xID:rN",
        help="Reference frame byte in the same log (e.g. 0x386:r0)",
    )
    _add_shared_hunt_args(parser)
    add_notation_arg(parser)
    parser.set_defaults(func=_run_can_log)
    return parser


def _add_uds_parser(kinds) -> argparse.ArgumentParser:
    parser = kinds.add_parser(
        "uds",
        help="Hunt a diagnostic PID's bytes vs a known signal (domain A)",
        description=(
            "Sweeps every byte offset of the target PID under every interpretation\n"
            "(u8/i16/u24/f32/... x endianness), time-aligns each candidate against a\n"
            "known reference signal on another ECU/PID (--against), and ranks them by\n"
            "|Pearson r|. Each top hit reports a linear fit (y=m·x+c), the residual,\n"
            "and a physical-unit guess — so you learn not just which byte, but its\n"
            "scale and offset.\n\n"
            "This automates the 'which byte tracks vehicle speed / motor RPM?' scratch\n"
            "work. Use --transform delta to hunt the byte tracking the reference's\n"
            "*rate* (e.g. torque vs acceleration), and --promote to write the top hit\n"
            "straight into ecus/ as an enabled, unverified candidate parameter.\n\n"
            "Read-only: analyses captures/ only, never talks to the device."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""\
examples:
  # which byte on AAF 2181 is vehicle speed (known on ESC 22C101)?
  canair hunt AAF 2181 --against ESC:22C101:REAL_SPEED_KMH

  # restrict to drive captures and only strong linear fits
  canair hunt AAF 2181 --against ESC:22C101:REAL_SPEED_KMH --state driving --min-n 30

  # hunt the byte that tracks the reference's *rate* (acceleration, not speed)
  canair hunt MCU 2102 --against ESC:22C101:REAL_SPEED_KMH --transform delta

  # rank by rank-correlation (catches quantized / saturating links)
  canair hunt MCU 2102 --against ESC:22C101:REAL_SPEED_KMH --method spearman

  # write the winning byte into ecus/ as an unverified candidate param
  canair hunt AAF 2181 --against ESC:22C101:REAL_SPEED_KMH --promote WHEEL_SPEED_KMH

  # reference an EXTERNAL log (calibrated meter / GPS / grid-voltage export),
  # joined by nearest timestamp on the captures' absolute clock
  canair hunt OBC 2101 --against-file grid_voltage.csv --state charging

  # confounder control: which byte tracks the grid once the IR-drop current is
  # regressed out? (partial correlation — surfaces links a dominant driver hides)
  canair hunt OBC 2101 --against-file grid_voltage.csv --control OBC:2101:OBC_DC_A

  # no reference at all: flag bytes whose scaled value lands in a physical band
  # (mains RMS/peak, line freq, 12V rail, HV pack) — finds an anchorless signal
  canair hunt OBC 2101 --physical --state charging

tip: --against takes a known signal ECU:PID:PARAM (or a raw ECU:PID:EXPR). Use
     `canair correlate --overlap` first to find a reference that actually shares
     time-aligned samples with your target.""",
    )
    parser.add_argument("ecu", nargs="?", help="Target ECU to hunt on (e.g. AAF)")
    parser.add_argument("pid", nargs="?", help="Target PID to hunt on (e.g. 2181)")
    ref_group = parser.add_mutually_exclusive_group(required=True)
    ref_group.add_argument(
        "--against",
        metavar="ECU:PID:PARAM",
        help="Reference signal: a diagnostic ECU:PID:PARAM (or ECU:PID:EXPR)",
    )
    ref_group.add_argument(
        "--against-file",
        dest="against_file",
        metavar="FILE",
        help="Reference from an external CSV (timestamp,value) instead of a bus "
        "signal — a calibrated meter log, GPS track, grid-voltage export. Joined "
        "by nearest timestamp; the file must be on the same absolute clock as the "
        "captures (relative/zero-based logs won't align)",
    )
    ref_group.add_argument(
        "--physical",
        action="store_true",
        help="No reference: flag bytes whose (scaled) value lands in a named "
        "physical band (mains RMS/peak, line freq, 12V rail, HV pack) at some "
        "scaling (/1 /10 /100 ×2 ×√2). Finds anchorless signals by plausibility",
    )
    _add_shared_hunt_args(parser)
    parser.add_argument(
        "--control",
        metavar="ECU:PID:PARAM",
        help="Confounder control: regress out this nuisance signal and rank by the "
        "PARTIAL correlation (what remains after removing the control's linear "
        "influence). Surfaces a byte whose link to --against only shows once the "
        "dominant driver is removed (e.g. AC voltage behind the IR-drop current)",
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
        help="Write the top hit's expression to ecus/ as an enabled, unverified "
        "candidate param NAME (via pids upsert-param), with the correlation "
        "evidence auto-filled into notes",
    )
    parser.add_argument(
        "--per-session",
        dest="per_session",
        action="store_true",
        help="Remove each recording session's DC baseline before ranking — makes a "
        "slowly-varying absolute-level reference/byte (pack/12V/mains voltage, a "
        "held temperature) rankable by --against instead of being dominated by "
        "cross-session offsets. Ranks the in-session *variation*, not the level",
    )
    parser.add_argument(
        "--session-gap",
        dest="session_gap",
        type=float,
        default=DEFAULT_SESSION_GAP_S,
        metavar="SECONDS",
        help=f"With --per-session: time gap that starts a new session (default {DEFAULT_SESSION_GAP_S}s)",
    )
    add_notation_arg(parser)
    add_scope_args(parser)
    parser.set_defaults(func=run)
    return parser


def _hit_label(h, notation: ByteNotation, sub_bytes: int) -> str:
    """Render a hunt hit's byte in ``notation``.

    WiCAN shows the promotable expression as-is (``h.expr``); other notations
    render the byte position (the expression stays available via --json/--promote,
    which always emit the canonical WiCAN form).
    """
    if notation is ByteNotation.WICAN:
        return h.expr
    return ByteRef.from_wican(h.offset, width=h.width).render(notation, sub_bytes=sub_bytes)


def _run_can_log(args) -> int:
    """Hunt on a raw broadcast-CAN frame log: which byte of --id tracks --against?

    ``--against`` is a frame byte ``0xID:rN`` in the same log (resolved from the
    log's frame series). Reuses the frame-domain sweep (frame_series.hunt_frame)
    and the shared ranking; hits are raw-CAN ``rN`` labels (no WiCAN expression,
    so --promote is rejected — frame signals are defined in signals/, Stage 4).
    """
    from pathlib import Path

    from canlib import frame_series
    from canlib.can_logs import CanLogError

    path = Path(args.file)
    if not path.is_file():
        print(f"hunt can: no such file: {path}", file=sys.stderr)
        return 1
    try:
        target = int(args.id, 16)
        id_filter = (
            frame_series.parse_id_filter(args.against.split(":")[0])
            if ":" in args.against
            else None
        )
        # Reference: a frame byte 0xID:rN in the same log.
        ref_series_map = frame_series.build_frame_series(
            path, args.can_format, min_distinct=2, id_filter=id_filter
        )
    except (ValueError, CanLogError) as e:
        print(f"hunt can: {e}", file=sys.stderr)
        return 1

    ref = ref_series_map.get(args.against)
    if ref is None:
        avail = ", ".join(sorted(ref_series_map)[:6])
        print(
            f"--against {args.against!r}: not a varying byte in {path.name}. "
            f"Expected a frame label like 0x386:r0. Available e.g.: {avail} …",
            file=sys.stderr,
        )
        return 1
    ref_label = args.against
    if args.transform and args.transform != "raw":
        ref = transform_ref(ref, args.transform)
        ref_label = f"{args.transform}({ref_label})"

    try:
        hits = frame_series.hunt_frame(
            path,
            args.can_format,
            target,
            ref,
            tol_s=args.join_tol,
            min_n=args.min_n,
            top=args.top,
            method=args.method,
            all_interps=args.all_interps,
        )
    except CanLogError as e:
        print(f"hunt can: {e}", file=sys.stderr)
        return 1

    if args.json:
        _json.dump(
            {
                "can_log": path.name,
                "target": f"0x{target:X}",
                "reference": ref_label,
                "hits": [
                    {
                        "expr": h.expr,
                        "interp": h.interp,
                        "offset": h.offset,
                        "r": h.r,
                        "n": h.n,
                        "slope": h.slope,
                        "intercept": h.intercept,
                        "resid": h.resid,
                        "unit_guess": h.unit_guess,
                    }
                    for h in hits
                ],
            },
            sys.stdout,
            indent=2,
        )
        print()
        return 0

    if not hits:
        print(f"No byte of 0x{target:X} correlates with {ref_label} in {path.name}.")
        return 0
    print(
        f"\n  {_BOLD}Hunt 0x{target:X} vs {ref_label}{_RESET} "
        f"{_DIM}({path.name}, nearest-join ≤{args.join_tol:g}s){_RESET}"
    )
    for h in hits:
        color = _GREEN if abs(h.r) >= 0.7 else _YELLOW if abs(h.r) >= 0.3 else _DIM
        unit = f"  {_CYAN}{h.unit_guess}{_RESET}" if h.unit_guess else ""
        print(
            f"    {color}r={h.r:+.3f}{_RESET}  {_BOLD}{h.expr}{_RESET} "
            f"{_DIM}({h.interp}){_RESET}  fit y={h.slope:.4f}·x{h.intercept:+.2f} "
            f"{_DIM}resid={h.resid:.2f} n={h.n}{_RESET}{unit}"
        )
    print()
    return 0


def run(args) -> int:
    from canlib.ecus import canonical_ecu_name_safe
    from canlib.profile import active
    from canlib.unit_guess import resolve_unit_candidates

    if not args.ecu or not args.pid:
        print(
            "hunt uds: ECU and PID are required (e.g. `hunt AAF 2181 --against …`).",
            file=sys.stderr,
        )
        return 2

    since, until, err = resolve_scope_bounds(args)
    if err:
        print(f"error: {err}", file=sys.stderr)
        return 2

    ecu = canonical_ecu_name_safe(args.ecu)
    pid = args.pid.upper()

    if args.physical:
        ignored = [
            flag
            for flag, active in (
                ("--control", args.control),
                ("--control-file", args.control_file),
                ("--transform", args.transform not in (None, "raw")),
                ("--method", args.method != "pearson"),
            )
            if active
        ]
        if ignored:
            print(
                f"warning: --physical ignores {', '.join(ignored)} "
                "(it scans value plausibility, not a reference correlation).",
                file=sys.stderr,
            )
        return _run_physical(args, ecu, pid, since, until)

    if args.control and args.control_file:
        print("error: --control and --control-file are mutually exclusive", file=sys.stderr)
        return 2

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

    if reference_is_bimodal([tp.value for tp in ref_series]):
        from canlib.stats import categorical_method_nudge

        print(
            f"hunt: warning: reference {ref_label!r} collapses into ~2 clusters "
            "(bimodal) \u2014 |r| then ranks cluster separation, not a signal match, so "
            "the top hits are unreliable. Use a scope with continuous variation "
            f"(a keep-all drive), not a bimodal regime flip.{categorical_method_nudge(args.method)} "
            "See docs/concepts/analysis-commands.md.",
            file=sys.stderr,
        )
    elif reference_is_absolute_level([tp.value for tp in ref_series]):
        print(
            f"hunt: warning: reference {ref_label!r} looks like a slowly-varying "
            "absolute level (small swing on a large baseline) \u2014 Pearson |r| is then "
            "corrupted by cross-session DC offsets, so --against ranking is unreliable "
            "for it. Prefer `hunt --physical` (named bands) plus comparing per-state "
            "absolute readings to a known value. See docs/concepts/analysis-commands.md.",
            file=sys.stderr,
        )

    control_series = None
    if args.control or args.control_file:
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

    loaded = load_signal_captures(
        [(ecu, pid)], since=since, until=until, state=args.state, label=args.label
    )
    lp = loaded[(ecu.upper(), pid)]
    if not lp.captures:
        print(
            f"No timed captures for {ecu} {pid} in scope"
            + (f" ({lp.n_no_time} untimed skipped)." if lp.n_no_time else "."),
            file=sys.stderr,
        )
        return 1

    hits = hunt_byte(
        lp,
        ref_series,
        tol_s=args.join_tol,
        min_n=args.min_n,
        top=args.top,
        method=args.method,
        all_interps=args.all_interps,
        control=control_series,
        ref_unit=None if args.against_file else ref_unit_for(args.against),
        candidates=resolve_unit_candidates(active().meta),
        per_session=args.per_session,
        session_gap_s=args.session_gap,
    )

    if args.promote:
        return _promote(args.promote, ecu, pid, hits, ref_label)

    if args.json:
        _json.dump(
            {
                "target": f"{ecu}:{pid}",
                "reference": ref_label,
                "join_tol_s": args.join_tol,
                "hits": [
                    {
                        "expr": h.expr,
                        "interp": h.interp,
                        "offset": h.offset,
                        "r": h.r,
                        "n": h.n,
                        "slope": h.slope,
                        "intercept": h.intercept,
                        "resid": h.resid,
                        "unit_guess": h.unit_guess,
                    }
                    for h in hits
                ],
            },
            sys.stdout,
            indent=2,
        )
        print()
        return 0

    if not hits:
        print(f"No byte on {ecu} {pid} correlates with {ref_label} in scope.")
        return 0
    notation = resolve_notation(args.notation)
    sub_bytes = subfunction_bytes_for_pid(pid)
    print(
        f"\n  {_BOLD}Hunt {ecu} {pid} vs {ref_label}{_RESET} "
        f"{_DIM}(nearest-join ≤{args.join_tol:g}s){_RESET}"
    )
    for h in hits:
        color = _GREEN if abs(h.r) >= 0.7 else _YELLOW if abs(h.r) >= 0.3 else _DIM
        unit = f"  {_CYAN}{h.unit_guess}{_RESET}" if h.unit_guess else ""
        label = _hit_label(h, notation, sub_bytes)
        print(
            f"    {color}r={h.r:+.3f}{_RESET}  {_BOLD}{label}{_RESET} "
            f"{_DIM}({h.interp}){_RESET}  fit y={h.slope:.4f}·x{h.intercept:+.2f} "
            f"{_DIM}resid={h.resid:.2f} n={h.n}{_RESET}{unit}"
        )
    print()
    return 0


def _run_physical(args, ecu: str, pid: str, since, until) -> int:
    """Reference-free physical-value band scan on one PID."""
    from canlib.grid_prompt import resolve_grid_region
    from canlib.physical_bands import resolve_physical_bands
    from canlib.profile import active
    from canlib.xanalysis import physical_scan

    loaded = load_signal_captures(
        [(ecu, pid)], since=since, until=until, state=args.state, label=args.label
    )
    lp = loaded[(ecu.upper(), pid)]
    if not lp.captures:
        print(
            f"No timed captures for {ecu} {pid} in scope"
            + (f" ({lp.n_no_time} untimed skipped)." if lp.n_no_time else "."),
            file=sys.stderr,
        )
        return 1

    bands = resolve_physical_bands(active().meta, grid_region=resolve_grid_region())
    hits = physical_scan(lp, min_n=args.min_n, top=args.top, bands=bands)

    if args.json:
        _json.dump(
            {
                "target": f"{ecu}:{pid}",
                "mode": "physical",
                "hits": [
                    {
                        "expr": h.expr,
                        "interp": h.interp,
                        "offset": h.offset,
                        "scaling": h.scaling,
                        "band": h.band,
                        "frac": h.frac,
                        "median": h.median,
                        "n": h.n,
                    }
                    for h in hits
                ],
            },
            sys.stdout,
            indent=2,
        )
        print()
        return 0

    if not hits:
        print(f"No byte on {ecu} {pid} lands in a known physical band in scope.")
        return 0
    notation = resolve_notation(args.notation)
    sub_bytes = subfunction_bytes_for_pid(pid)
    print(
        f"\n  {_BOLD}Physical-band scan {ecu} {pid}{_RESET} "
        f"{_DIM}({len(lp.captures)} timed captures){_RESET}"
    )
    for h in hits:
        color = _GREEN if h.frac >= 0.9 else _YELLOW if h.frac >= 0.7 else _DIM
        if notation is ByteNotation.WICAN:
            label = h.expr
        else:
            label = ByteRef.from_wican(h.offset, width=h.width).render(
                notation, sub_bytes=sub_bytes
            )
        print(
            f"    {color}{h.frac * 100:3.0f}% in-band{_RESET}  {_BOLD}{h.scaling}·{label}{_RESET} "
            f"{_DIM}({h.interp}){_RESET}  {_CYAN}{h.band}{_RESET} "
            f"{_DIM}median≈{h.median:.1f} n={h.n}{_RESET}"
        )
    print()
    return 0


def _promote(name: str, ecu: str, pid: str, hits, ref_label: str) -> int:
    """Write the top hit as an enabled, unverified candidate param.

    Routes through the shared guarded write (``_promote.write_candidate``) — the
    same snapshot → edit → schema-validate → auto-revert gate as ``canair pids
    upsert-param`` — so a PCI-crossing expression is rejected and rolled back.
    """
    from canlib.commands._promote import print_promoted, write_candidate
    from canlib.pids_edit import PidsEditError

    if not hits:
        print("Nothing to promote — no correlating byte found.", file=sys.stderr)
        return 1
    top = hits[0]
    if top.expr == "<no-expr>":
        print(
            f"Top hit ({top.interp} @ B{top.offset}) has no WiCAN expression "
            "(float/LE-signed) — cannot promote. Try a byte-expressible interpretation.",
            file=sys.stderr,
        )
        return 1
    unit_note = f" {top.unit_guess}" if top.unit_guess else ""
    notes = (
        f"Candidate from `canair hunt` vs {ref_label}: r={top.r:+.3f} (n={top.n}), "
        f"fit y={top.slope:.4f}·x{top.intercept:+.2f}, resid={top.resid:.2f}.{unit_note} "
        "Enabled unverified — confirm scale/sign against reality."
    )

    try:
        fpath = write_candidate(
            ecu, pid, name, top.expr, source=f"canair hunt vs {ref_label}", notes=notes
        )
    except (PidsEditError, SystemExit) as e:
        print(f"promote failed: {e}", file=sys.stderr)
        return 1
    print_promoted(ecu, pid, name, top.expr, top.r, fpath)
    return 0
