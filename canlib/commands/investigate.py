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

from canlib.align import (
    DEFAULT_JOIN_TOL_S,
    PreparedSeries,
    TimePoint,
    join_prepared,
    load_signal_captures,
    prepare_series,
)
from canlib.byteindex import mapped_bits, mapped_offsets
from canlib.capture_dates import add_scope_args, resolve_scope_bounds
from canlib.commands._group import group_help
from canlib.keepmode import scope_is_keep_unique
from canlib.notation import (
    add_notation_arg,
    relabel_signal,
    resolve_notation,
    subfunction_bytes_for_pid,
)
from canlib.triage import WordCandidate, detect_words, triage_byte
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
        help="Explain an unknown signal: uds (a PID) | can (an arbitration ID in a frame log)",
        description=(
            "Point this at an unknown signal and get one ranked table telling you\n"
            "everything worth knowing about each of its bytes. Choose a domain kind:\n"
            "  uds   a diagnostic PID (default) — per byte: mapped? / state F /\n"
            "        best co-polled anchor / unit guess (domain A). A bare\n"
            "        `canair investigate MCU 2102` is shorthand for this.\n"
            "  can   an arbitration ID in a raw broadcast-CAN frame log (domain B) —\n"
            "        per byte: best cross-ID anchor + linear fit + unit guess.\n\n"
            "Read-only: analyses captures/ only, never talks to the device."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    kinds = parser.add_subparsers(dest="investigate_kind", metavar="<kind>")
    _add_uds_parser(kinds)
    _add_can_parser(kinds)
    parser.set_defaults(
        func=group_help("_investigate_group_parser"), _investigate_group_parser=parser
    )
    return parser


def _add_uds_parser(kinds) -> argparse.ArgumentParser:
    parser = kinds.add_parser(
        "uds",
        help="Explain a diagnostic PID (domain A)",
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

  # active-but-independent: rank bytes that separate by state yet DON'T track a
  # named driver — the fingerprint of AC voltage vs charge current
  canair investigate OBC 2101 --independent-of OBC:2101:OBC_DC_A --state charging

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
    parser.add_argument(
        "--field",
        metavar="NAME",
        help="With --events: track ONE defined param (a typed enum/bitmask/struct "
        "date field) as a single logical signal — emit one transition per change of "
        "its DECODED value (e.g. {Mon 08:00}->{Tue 07:30}), not scattered per-byte "
        "edges. NAME is a parameter of the target ECU:PID.",
    )
    parser.add_argument(
        "--independent-of",
        dest="independent_of",
        metavar="ECU:PID:PARAM",
        help="Rank bytes that separate by state yet DON'T track this driver signal "
        "— the 'active-but-independent' finder (e.g. AC voltage: varies while "
        "charging but is uncorrelated with charge current). Adds a driver-r column "
        "and re-ranks by state separation weighted by independence from the driver",
    )
    parser.add_argument(
        "--independent-of-file",
        dest="independent_of_file",
        metavar="FILE",
        help="Like --independent-of, but the driver is an external timestamp,value "
        "CSV (mutually exclusive with --independent-of)",
    )
    parser.add_argument("--json", action="store_true", help="Machine-readable output")
    add_notation_arg(parser)
    add_scope_args(parser)
    parser.set_defaults(func=run)
    return parser


def _add_can_parser(kinds) -> argparse.ArgumentParser:
    parser = kinds.add_parser(
        "can",
        help="Explain an arbitration ID in a raw broadcast-CAN frame log (domain B)",
        description=(
            "Explain one arbitration ID in a raw broadcast-CAN frame log: for every\n"
            "varying data byte, report its strongest cross-ID anchor (Pearson r +\n"
            "linear fit y=m·x+c) and a physical-unit guess, ranked strongest first.\n\n"
            "The domain-B analogue of `investigate uds`: frames have no defined-param\n"
            "mapping (signals live in signals/, decoded via Stage-4 tooling) and no\n"
            "power-state metadata, so the report is anchor-centric. Bytes are\n"
            "labelled 0xID:rN (raw-CAN space, no PCI). Read-only.\n\n"
            "To pin the exact byte against a known reference use `canair hunt can`;\n"
            "to see every relationship at once use `canair correlate can`."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""\
examples:
  canair investigate can drive.blf --id 0x386        # rank each byte of 0x386 by best cross-ID anchor
  canair investigate can drive.csv --id 0x220 --bits # include toggling bits
  canair investigate can drive.asc --id 0x386 --json # machine-readable""",
    )
    parser.add_argument(
        "file",
        metavar="FILE",
        help="Path to a raw broadcast-CAN frame log (.asc/.blf/candump .log/.trc/GVRET .csv)",
    )
    parser.add_argument(
        "--id",
        required=True,
        metavar="ID",
        help="Arbitration ID to explain (e.g. 0x386)",
    )
    parser.add_argument(
        "--can-format",
        choices=["auto", "asc", "blf", "csv", "log", "gvret"],
        default="auto",
        help="Log format (default: auto-detect by extension)",
    )
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
        "--bits",
        action="store_true",
        help="Also analyse individual toggling bits (rN:k)",
    )
    parser.add_argument("--json", action="store_true", help="Machine-readable output")
    parser.set_defaults(func=_run_can)
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
    driver_r: float | None = None  # |r| vs the --independent-of driver, if set
    independence: float | None = None  # state_f weighted by (1 - |driver_r|)
    physical: str | None = None  # named physical band this byte's value lands in
    kind: str | None = None  # triage class: constant/counter/checksum/enum/continuous
    entropy: float | None = None  # Shannon entropy (bits) of the byte's values
    lag1: float | None = None  # lag-1 (sample) autocorrelation

    @property
    def label(self) -> str:
        return f"B{self.offset}:{self.bit}" if self.bit is not None else f"B{self.offset}"


def _independence_score(state_f: float | None, driver_r: float | None) -> float | None:
    """State separation weighted by independence from the driver.

    High when a byte separates cleanly by state (large ``state_f``) *and* barely
    tracks the driver (small ``|driver_r|``) — the "active but independent"
    fingerprint. ``None`` when there is no state separation to rank by. A missing
    ``driver_r`` (no time overlap with the driver) counts as fully independent.
    """
    if state_f is None:
        return None
    f = 1e6 if state_f == float("inf") else state_f
    return f * (1.0 - min(1.0, abs(driver_r or 0.0)))


def _state_f(frames_by_state: dict[str, list[float]]):
    from canlib.xanalysis import discriminability

    return discriminability(frames_by_state)


def run(args) -> int:
    from canlib.commands.correlate import _discover_specs
    from canlib.ecus import canonical_ecu_name_safe
    from canlib.pids import build_ecu_index, load_pids
    from canlib.xanalysis import byte_state_buckets as _byte_state_buckets

    since, until, err = resolve_scope_bounds(args)
    if err:
        print(f"error: {err}", file=sys.stderr)
        return 2

    ecu = canonical_ecu_name_safe(args.ecu)
    pid = args.pid.upper()

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
        _print_events(ecu, pid, lp, mapped, mapped_bit, args, params_def)
        return 0

    # Physical-band hits per starting offset (plausibility, needs no reference).
    # Computed after the --events short-circuit so it isn't wasted there.
    from canlib.xanalysis import physical_scan

    physical_by_off = {h.offset: f"{h.scaling}·{h.expr} ≈ {h.band}" for h in physical_scan(lp)}

    # --independent-of: load a driver signal to rank bytes by independence from.
    driver_series = None
    driver_label = None
    if args.independent_of and args.independent_of_file:
        print(
            "error: --independent-of and --independent-of-file are mutually exclusive",
            file=sys.stderr,
        )
        return 2
    if args.independent_of or args.independent_of_file:
        from canlib.xanalysis import load_ref

        try:
            if args.independent_of_file:
                from canlib.align import load_reference_file

                driver_series, driver_label = load_reference_file(args.independent_of_file)
            else:
                driver_series, driver_label = load_ref(
                    args.independent_of, since=since, until=until, state=args.state, label=args.label
                )
        except ValueError as e:
            flag = "--independent-of-file" if args.independent_of_file else "--independent-of"
            print(f"{flag} error: {e}", file=sys.stderr)
            return 1

    # Anchor signals: every param on the OTHER co-polled ECU/PIDs in scope.
    anchors: dict[str, list] = {}
    other_specs = [
        s
        for s in _discover_specs(None, since, until, args.state, args.label)
        if s != (ecu.upper(), pid)
    ]
    if other_specs:
        aloaded = load_signal_captures(
            other_specs, since=since, until=until, state=args.state, label=args.label
        )
        for (aecu, apid), alp in aloaded.items():
            if not alp.captures:
                continue
            aparams = ecu_index.get(aecu, {}).get("pids", {}).get(apid, {}).get("parameters", {})
            anchors.update(build_param_series(alp, aparams))

    # Prepare anchor + driver series ONCE (sort + flatten to float epoch arrays);
    # _best_anchor/_driver_r are called per target byte/bit, so re-preparing these
    # inside them would re-sort every anchor for every byte (O(bytes * anchors)).
    anchors_prepared = {label: prepare_series(s) for label, s in anchors.items()}
    driver_prepared = prepare_series(driver_series) if driver_series is not None else None

    reports: list[_ByteReport] = []
    for key, series in target.items():
        off = int(key.rsplit(":B", 1)[1])
        best = _best_anchor(series, anchors_prepared, args.join_tol, args.min_n)
        sb = state_buckets.get(f"B{off}")
        m = mapped.get(off)
        sf = _state_f(sb) if sb else None
        dr = _driver_r(series, driver_prepared, args.join_tol, args.min_n)
        tri = triage_byte([int(tp.value) for tp in series])
        reports.append(
            _ByteReport(
                offset=off,
                mapped_by=m[0] if m else None,
                mapped_verified=m[1] if m else False,
                state_f=sf,
                anchor=best[0] if best else None,
                anchor_r=best[1] if best else None,
                anchor_n=best[2] if best else 0,
                slope=best[3] if best else None,
                intercept=best[4] if best else None,
                unit_guess=best[5] if best else None,
                driver_r=dr,
                independence=_independence_score(sf, dr),
                physical=physical_by_off.get(off),
                kind=tri.kind,
                entropy=tri.entropy,
                lag1=tri.lag1,
            )
        )

    # Probable multi-byte words: a near-constant hi byte next to a full-range lo
    # byte (how a scaled voltage hides across a byte boundary). Built from ALL
    # non-PCI data bytes (min_distinct=1) — NOT the min_distinct=2 report set —
    # so a near-constant hi byte isn't dropped and consecutive columns are truly
    # ISO-TP-adjacent (PCI-straddling pairs included, filter gaps excluded).
    word_cols = [
        (int(k.rsplit(":B", 1)[1]), [int(tp.value) for tp in s])
        for k, s in build_byte_series(lp, min_distinct=1).items()
    ]
    # Keep only pairs that render to a real adjacent word expression (drops any
    # non-adjacent pair rather than emit a misleading [Bhi:Blo] spanning a gap).
    word_candidates = [
        (w, expr) for w in detect_words(word_cols) if (expr := _word_expr(w)) is not None
    ]

    if args.bits:
        for key, series in build_bit_series(lp).items():
            off, bit = _parse_bit_key(key)
            best = _best_anchor(series, anchors_prepared, args.join_tol, args.min_n)
            sb = state_buckets.get(f"B{off}:{bit}")
            m = mapped_bit.get((off, bit))
            sf = _state_f(sb) if sb else None
            dr = _driver_r(series, driver_prepared, args.join_tol, args.min_n)
            reports.append(
                _ByteReport(
                    offset=off,
                    mapped_by=m[0] if m else None,
                    mapped_verified=m[1] if m else False,
                    state_f=sf,
                    anchor=best[0] if best else None,
                    anchor_r=best[1] if best else None,
                    anchor_n=best[2] if best else 0,
                    slope=best[3] if best else None,
                    intercept=best[4] if best else None,
                    unit_guess=best[5] if best else None,
                    bit=bit,
                    driver_r=dr,
                    independence=_independence_score(sf, dr),
                )
            )

    if not args.all:
        # Hide only positions a *verified* param already decodes; unverified-mapped
        # positions are unfinished work, so surface them alongside unmapped ones.
        reports = [r for r in reports if not r.mapped_verified]
    if driver_series is not None:
        # Independence ranking: state separation, discounted by how much the byte
        # tracks the named driver (the active-but-independent finder).
        reports.sort(key=lambda r: -(r.independence or 0))
    else:
        # Rank: strongest anchor first, then state separation.
        reports.sort(key=lambda r: (-(abs(r.anchor_r or 0)), -(r.state_f or 0)))

    if args.json:
        _json.dump(
            {
                "target": f"{ecu}:{pid}",
                "join_tol_s": args.join_tol,
                "independent_of": driver_label,
                "keep_unique": scope_is_keep_unique(lp.captures),
                "bytes": [vars(r) for r in reports],
                "word_candidates": [
                    {"expr": expr, "score": w.score} for w, expr in word_candidates
                ],
            },
            sys.stdout,
            indent=2,
            default=str,
        )
        print()
        return 0

    _print_report(
        ecu, pid, reports, args, lp, bool(anchors), driver_label=driver_label, words=word_candidates
    )
    return 0


def _run_can(args) -> int:
    """Explain one arbitration ID in a frame log — per-byte best cross-ID anchor.

    Domain-B analogue of ``run``: build the target ID's byte (and, with --bits,
    bit) series and every OTHER ID's series as anchors, then rank each target
    byte by its strongest cross-ID correlation (r + linear fit + unit guess).
    """
    from pathlib import Path

    from canlib import frame_series
    from canlib.can_logs import CanLogError

    path = Path(args.file)
    if not path.is_file():
        print(f"investigate can: no such file: {path}", file=sys.stderr)
        return 1
    try:
        target_ids = frame_series.parse_id_filter(args.id)
        if target_ids is None or len(target_ids) != 1:
            print(
                f"investigate can: --id must be a single arbitration ID, got {args.id!r}",
                file=sys.stderr,
            )
            return 1
        # Build ALL varying series once (target + anchors share the single read).
        if args.bits:
            all_series = frame_series.build_frame_bit_series(path, args.can_format)
        else:
            all_series = frame_series.build_frame_series(path, args.can_format, min_distinct=4)
    except (ValueError, CanLogError) as e:
        print(f"investigate can: {e}", file=sys.stderr)
        return 1

    target_label = frame_series._id_label(next(iter(target_ids)))
    target = {n: s for n, s in all_series.items() if n.split(":", 1)[0] == target_label}
    anchors = {n: s for n, s in all_series.items() if n.split(":", 1)[0] != target_label}
    if not target:
        print(
            f"investigate can: {target_label} has no varying "
            f"{'bits' if args.bits else 'bytes'} in {path.name}.",
            file=sys.stderr,
        )
        return 1

    rows = []
    anchors_prepared = {label: prepare_series(s) for label, s in anchors.items()}
    for name, series in sorted(target.items()):
        best = _best_anchor(series, anchors_prepared, args.join_tol, args.min_n)
        rows.append((name, best))
    # Rank strongest anchor first; unanchored bytes last.
    rows.sort(key=lambda t: -(abs(t[1][1]) if t[1] else 0.0))

    if args.json:
        _json.dump(
            {
                "can_log": path.name,
                "id": target_label,
                "join_tol_s": args.join_tol,
                "bytes": [
                    {
                        "signal": name,
                        "anchor": best[0] if best else None,
                        "r": best[1] if best else None,
                        "n": best[2] if best else 0,
                        "slope": best[3] if best else None,
                        "intercept": best[4] if best else None,
                        "unit_guess": best[5] if best else None,
                    }
                    for name, best in rows
                ],
            },
            sys.stdout,
            indent=2,
            default=str,
        )
        print()
        return 0

    print(
        f"\n  {_BOLD}Investigate {target_label}{_RESET} "
        f"{_DIM}({path.name}, {len(target)} varying "
        f"{'bits' if args.bits else 'bytes'}, ≤{args.join_tol:g}s join){_RESET}"
    )
    shown = False
    for name, best in rows:
        if best and best[1] is not None and abs(best[1]) >= args.min_r:
            _label, r, n, m, c, unit = best
            rc = _GREEN if abs(r) >= 0.7 else _YELLOW
            fit = f" fit y={m:.4f}·x{c:+.2f}" if m is not None else ""
            unit_s = f" {_CYAN}{unit}{_RESET}" if unit else ""
            print(
                f"    {_BOLD}{name}{_RESET}  {rc}r={r:+.3f}{_RESET} vs {best[0]} "
                f"{_DIM}n={n}{fit}{_RESET}{unit_s}"
            )
            shown = True
        else:
            print(f"    {_BOLD}{name}{_RESET}  {_DIM}no cross-ID anchor ≥ {args.min_r}{_RESET}")
    if not shown:
        print(
            f"    {_DIM}no byte cleared |r| ≥ {args.min_r} — lower --min-r, or the ID may "
            f"carry only counters/constants.{_RESET}"
        )
    print()
    return 0


def _parse_bit_key(key: str) -> tuple[int, int]:
    """``ECU:PID:B10:5`` → ``(10, 5)`` — the last two colon fields are offset:bit."""
    _, off, bit = key.rsplit(":", 2)
    return int(off.lstrip("B")), int(bit)


# The strongest-anchor result: (label, r, n, slope, intercept, unit_guess).
_AnchorHit = tuple[str, float, int, float | None, float | None, str | None]


def _best_anchor(
    series: list[TimePoint],
    anchors_prepared: dict[str, PreparedSeries],
    tol: float,
    min_n: int,
) -> _AnchorHit | None:
    """The strongest-correlating anchor for one byte series → (label,r,n,m,c,unit).

    ``anchors_prepared`` maps label → :class:`PreparedSeries` (built once by the
    caller); the target ``series`` is prepared once here and joined against each
    anchor, so the same anchor is never re-sorted/re-flattened per target byte.
    """
    if not anchors_prepared:
        return None
    ps = prepare_series(series)
    best: _AnchorHit | None = None
    for label, aprep in anchors_prepared.items():
        xs, ys, n = join_prepared(aprep, ps, tol_s=tol)
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


def _driver_r(
    series: list[TimePoint],
    driver_prepared: PreparedSeries | None,
    tol: float,
    min_n: int,
) -> float | None:
    """Correlation of one byte/bit series vs the --independent-of driver, or None.

    ``driver_prepared`` is a :class:`PreparedSeries` (built once) or ``None``.
    """
    if driver_prepared is None:
        return None
    xs, ys, n = join_prepared(driver_prepared, prepare_series(series), tol_s=tol)
    if n < min_n:
        return None
    return correlation(xs, ys)


def _word_expr(word: WordCandidate) -> str | None:
    """WiCAN big-endian word expression for a detected (hi, lo) offset pair.

    Returns ``None`` when the pair is **not** ISO-TP-adjacent (a gap byte sits
    between them) — such a pair isn't a real 16-bit word and must not be rendered
    as a misleading ``[Bhi:Blo]`` range. Adjacent pairs (including PCI-straddling
    ones) render via :class:`ByteRef`, which emits the correct shift-composition.
    """
    from canlib.byteindex import wican_to_isotp
    from canlib.notation import ByteRef

    hi_key, lo_key = word.hi_key, word.lo_key
    if not (isinstance(hi_key, int) and isinstance(lo_key, int)):
        return None  # only int byte-offset keys form a UDS word expression
    hi_iso = wican_to_isotp(hi_key)
    lo_iso = wican_to_isotp(lo_key)
    if hi_iso is None or lo_iso is None or lo_iso != hi_iso + 1:
        return None
    return ByteRef.from_isotp(hi_iso, width=2).to_wican_expression()


def _print_report(
    ecu, pid, reports, args, lp, has_anchors: bool, *, driver_label=None, words=None
) -> None:
    notation = resolve_notation(args.notation)
    sub_bytes = subfunction_bytes_for_pid(pid)
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
            anchor_label = relabel_signal(r.anchor, notation)
            anchor = f"  {rc}r={r.anchor_r:+.3f}{_RESET} vs {anchor_label} {_DIM}n={r.anchor_n}{fit}{_RESET}{unit}"
        drv = ""
        if driver_label is not None:
            # Low |driver r| = independent of the driver (the signal we want).
            dv = "—" if r.driver_r is None else f"{r.driver_r:+.3f}"
            dc = _GREEN if (r.driver_r is None or abs(r.driver_r) < 0.3) else _DIM
            drv = f"  {dc}drv={dv}{_RESET}"
        phys = f"  {_CYAN}{r.physical}{_RESET}" if r.physical else ""
        kind = ""
        if r.kind and r.bit is None and r.kind != "continuous":
            # Flag the non-analog byte classes (constant/counter/checksum/enum);
            # "continuous" is the unremarkable default, left unlabelled.
            kind = f"  {_DIM}{r.kind}{_RESET}"
        print(
            f"    {_BOLD}{relabel_signal(r.label, notation, sub_bytes=sub_bytes)}{_RESET} "
            f"{tag}{f_str}{drv}{anchor}{phys}{kind}"
        )
    if words:
        print(
            f"\n    {_BOLD}probable multi-byte words{_RESET} "
            f"{_DIM}(near-constant hi byte + full-range lo byte){_RESET}"
        )
        for w, expr in words[:6]:
            print(f"      {_CYAN}{expr}{_RESET}  {_DIM}score={w.score:.2f}{_RESET}")
    if driver_label is not None:
        print(
            f"    {_DIM}ranked by state separation × independence from {driver_label} "
            f"(drv = |r| vs that driver; low drv + high stateF = the target).{_RESET}"
        )
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


def _iter_field_edges(lp, param: dict):
    """Yield (dt, before_display, after_display, cap) per change of a typed param's
    DECODED value across timed captures — one logical transition per field change.

    This collapses a multi-byte/enum/bitmask/struct field into a single signal,
    so a schedule/mode change reads as ``{Mon 08:00}->{Tue 07:30}`` rather than
    scattered per-byte edges.
    """
    from canlib.byteindex import payload_to_wican_bytes
    from canlib.capture_dates import entry_datetime
    from canlib.decode_value import decode_typed, render

    frames = []
    for cap in lp.captures:
        dt = entry_datetime(cap)
        if dt is None:
            continue
        try:
            fr = payload_to_wican_bytes(cap["payload"])
        except Exception:
            continue
        frames.append((dt, fr, cap))
    frames.sort(key=lambda t: t[0])

    edges = []
    prev: str | None = None
    for dt, fr, cap in frames:
        disp = render(decode_typed(param, fr), param.get("unit", ""))
        if prev is not None and disp != prev:
            edges.append((dt, prev, disp, cap))
        prev = disp
    return edges


def _print_field_events(ecu, pid, lp, args, param: dict) -> None:
    """Logical-transition timeline for one typed field (--events --field NAME)."""
    edges = _iter_field_edges(lp, param)
    if args.json:
        _json.dump(
            {
                "target": f"{ecu}:{pid}",
                "field": args.field,
                "keep_unique": scope_is_keep_unique(lp.captures),
                "events": [
                    {
                        "time": e[0].strftime("%H:%M:%S"),
                        "before": e[1],
                        "after": e[2],
                        "note": _cap_note(e[3]),
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
        f"\n  {_BOLD}Events {ecu} {pid} · {args.field}{_RESET} "
        f"{_DIM}({len(lp.captures)} timed captures){_RESET}"
    )
    _print_keep_banner(lp.captures)
    if not edges:
        print(f"    {_DIM}no transitions of {args.field} in scope.{_RESET}\n")
        return
    for dt, before, after, cap in edges:
        note = _cap_note(cap)
        note_str = f"  {_DIM}~ note: {_CYAN}{note}{_RESET}" if note else ""
        print(
            f"    {_DIM}{dt.strftime('%H:%M:%S')}{_RESET}  "
            f"{_BOLD}{before}{_RESET} → {_BOLD}{after}{_RESET}{note_str}"
        )
    print()


def _print_events(ecu, pid, lp, mapped, mapped_bit, args, params_def=None) -> None:
    """Edge/event-timeline view: each transition with its time and nearest note."""
    if args.field:
        params_def = params_def or {}
        param = params_def.get(args.field)
        if param is None:
            print(
                f"\n  {_YELLOW}investigate: no parameter {args.field!r} on {ecu} {pid}.{_RESET}\n",
                file=sys.stderr,
            )
            return
        _print_field_events(ecu, pid, lp, args, param)
        return
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
    notation = resolve_notation(args.notation)
    sub_bytes = subfunction_bytes_for_pid(pid)
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
        shown = relabel_signal(label, notation, sub_bytes=sub_bytes)
        print(
            f"    {_DIM}{dt.strftime('%H:%M:%S')}{_RESET}  {_BOLD}{shown}{_RESET} {arrow}  {tag}{note_str}"
        )
    print()


def _cap_note(cap) -> str:
    """The best free-text note for a capture: its own note, else the session's."""
    return str(cap.get("notes") or cap.get("session_notes") or "").strip()
