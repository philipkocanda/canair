"""``investigate --counters`` — monotonic counter detection over one PID.

Bridges the pure sweep in :mod:`canlib.counters` to the diagnostic capture model:
builds a **row-aligned ISO-TP payload matrix**, runs the sweep, then maps each
winning window back to a WiCAN expression and overlays which parameter (if any)
already decodes it.

Why ISO-TP space: a multi-byte counter's bytes must be genuinely *contiguous*, and
in WiCAN space they are not — ISO-TP PCI framing bytes interleave every 7 data
bytes (a 4-byte counter can straddle one). ISO-TP payload indices are contiguous
by construction, so windows are formed there and rendered to WiCAN via
:class:`~canlib.notation.ByteRef`, which emits the correct shift-composition for a
PCI-straddling read.
"""

from __future__ import annotations

import json as _json
import sys
from dataclasses import dataclass
from datetime import datetime

from canlib.align import DEFAULT_SESSION_GAP_S, LoadedPid
from canlib.byteindex import mapped_offsets, wican_to_isotp
from canlib.capture_dates import active_scope_flags
from canlib.counters import (
    SCAN_FLOOR_BITS,
    CounterCandidate,
    cluster_counters,
    find_counters,
)
from canlib.keepmode import scope_is_keep_changes, scope_is_keep_unique
from canlib.notation import ByteNotation, ByteRef, resolve_notation, subfunction_bytes_for_pid
from canlib.triage import bit_flip_rates

from .render import print_keep_banner

# Fraction of captures that must reach the common prefix length (see
# _payload_matrix). Below this, a payload is treated as too short to align.
MIN_LEN_FRAC = 0.9

_BOLD = "\033[1m"
_DIM = "\033[2m"
_GREEN = "\033[92m"
_YELLOW = "\033[93m"
_CYAN = "\033[96m"
_RESET = "\033[0m"

_KIND_TITLES = {
    "accumulator": (
        "ACCUMULATORS",
        "non-decreasing across the corpus, moving within sessions "
        "(odometer / operating-seconds / cumulative Ah-Wh)",
    ),
    "cycle": (
        "CYCLE COUNTERS",
        "non-decreasing but FLAT within every session, stepping only between them "
        "(ignition / power-cycle / trip count)",
    ),
    "timer": (
        "RUN TIMERS",
        "monotonic per session, resetting to ~0, tracking wall-clock "
        "(uptime / seconds-since-key-on)",
    ),
}


def _payload_matrix(lp: LoadedPid) -> tuple[list[datetime], list[bytes]]:
    """Time-ordered ISO-TP payloads truncated to a common prefix length.

    Aligned on a common **prefix**, not the modal length. An ISO-TP response is
    frequently padded (a trailing 0xAA), so the same field sits at the same offset
    in a 12- and a 13-byte payload; filtering to the modal length discarded 478 of
    BCM 22C011's 3524 captures — three months of the horizon a long-run counter
    search depends on. Takes the longest prefix that :data:`MIN_LEN_FRAC` of
    captures reach and keeps every capture reaching it.
    """
    rows = [(d.dt, d.payload_bytes) for d in lp.decoded if d.dt is not None]
    rows.sort(key=lambda r: r[0])
    if not rows:
        return [], []
    lens = sorted(len(p) for _dt, p in rows)
    usable = lens[max(0, int(len(lens) * (1.0 - MIN_LEN_FRAC)))]
    kept = [(dt, p[:usable]) for dt, p in rows if len(p) >= usable]
    if not kept or usable < 1:
        return [], []
    return [dt for dt, _p in kept], [p for _p_dt, p in kept]


def _expression(cand: CounterCandidate) -> str | None:
    """WiCAN expression for a candidate's ISO-TP window, or None if unrenderable."""
    offsets = [k for k in cand.keys if isinstance(k, int)]
    if len(offsets) != len(cand.keys):
        return None
    ref = ByteRef.from_isotp(min(offsets), width=len(offsets), little=cand.little)
    return ref.to_wican_expression()


def _label(cand: CounterCandidate) -> str:
    offsets = [k for k in cand.keys if isinstance(k, int)]
    if not offsets:
        return "?"
    lo, hi = min(offsets), max(offsets)
    tail = f"-i{hi}" if hi > lo else ""
    end = "LE" if cand.little and cand.width > 1 else ""
    return f"i{lo}{tail}{end}"


def _display_label(cand: CounterCandidate, notation: ByteNotation, sub_bytes: int) -> str:
    """Human-view label for a window, re-rendered in ``notation``.

    ISO-TP is the label's canonical space (:func:`_label`): it is compact, names
    the contiguous window unambiguously, and carries the endianness suffix. The
    WiCAN form is already the expression column, so ``wican`` (and ``isotp``) keep
    the canonical ISO-TP label — an "ISO-TP label beside the WiCAN expression" is
    the intended default. Only ``torque``/``bix`` re-render, for cross-referencing
    an external PID sheet. ``--json``/``--promote`` never relabel (they carry the
    canonical WiCAN expression and the ISO-TP label), per the notation policy.
    """
    if notation not in (ByteNotation.TORQUE, ByteNotation.BIX):
        return _label(cand)
    offsets = [k for k in cand.keys if isinstance(k, int)]
    if not offsets:
        return _label(cand)
    ref = ByteRef.from_isotp(min(offsets), width=len(offsets), little=cand.little)
    return ref.render(notation, sub_bytes=sub_bytes)


def _mapped_by(
    cand: CounterCandidate, mapped: dict[int, tuple[str, bool]]
) -> tuple[str | None, bool]:
    """Which defined params cover this window, and is every one of them verified?

    A window is only *settled* when every covering param is verified. One
    unverified guess leaves it open work — which matters because a counter is
    frequently the evidence that refutes such a guess (a byte labelled as a
    user-set hour that in fact only ever rises), so `--unmapped-only` must not
    hide it. Same three-way distinction the byte view draws with `[NAME?]`.
    """
    hits = [mapped[k] for k in cand.keys if isinstance(k, int) and k in mapped]
    names = sorted({name for name, _verified in hits})
    if not names:
        return None, False
    return ", ".join(names), all(verified for _name, verified in hits)


def _fmt(value: float) -> str:
    return f"{value:.0f}" if abs(value) >= 1000 or value == int(value) else f"{value:.2f}"


def _as_json(cand: CounterCandidate, expr: str | None, mapped: tuple[str | None, bool]) -> dict:
    mapped_by, mapped_verified = mapped
    return {
        "kind": cand.kind,
        "expression": expr,
        "label": _label(cand),
        "isotp_offsets": list(cand.keys),
        "little_endian": cand.little,
        "width": cand.width,
        "bits": round(cand.bits, 2),
        "n": cand.n,
        "n_distinct": cand.n_distinct,
        "n_up": cand.n_up,
        "n_down": cand.n_down,
        "n_varying": cand.n_varying,
        "canonical": cand.canonical,
        "first": cand.first,
        "last": cand.last,
        "delta": cand.total_delta,
        "med_step": cand.med_step,
        "max_step": cand.max_step,
        "msb_jump": round(cand.msb_jump, 4),
        "step_ratio": round(cand.step_ratio, 6),
        "span_days": round(cand.span_s / 86400, 2),
        "per_year": cand.per_year,
        "n_sessions": cand.n_sessions,
        "flat_sessions": cand.flat_sessions,
        "boundary_steps": cand.boundary_steps,
        "mapped_by": mapped_by,
        "mapped_verified": mapped_verified,
        "tick": cand.tick,
        "tick_err": cand.tick_err,
        "slope_cv": cand.slope_cv,
        "reset_frac": cand.reset_frac,
        "sessions": [vars(f) for f in cand.sessions],
    }


def run_counters(ecu: str, pid: str, lp: LoadedPid, args, params_def: dict) -> int:
    """Detect and report monotonic counters on one ECU:PID."""
    # --counters wants the WHOLE history — the calendar span is the evidence, and a
    # slow counter is flat within any one session — so a scope filter silently
    # understates the `bits` ranking and can skew the empty-path threshold advice.
    # Warn loudly (stderr, and `scoped` in --json) whenever one is set.
    scope_flags = active_scope_flags(args)
    if scope_flags:
        print(warn_scoped_counters(scope_flags), file=sys.stderr)

    scan = scan_counters(pid, lp, args, params_def)
    if scan is None:
        usable = sum(1 for d in lp.decoded if d.dt is not None)
        print(
            f"Not enough timed captures for {ecu} {pid} to test monotonicity ({usable} usable).",
            file=sys.stderr,
        )
        return 1

    notation = resolve_notation(args.notation)
    sub_bytes = subfunction_bytes_for_pid(pid)
    groups, best_below, mapped, dts, n_days = (
        scan.groups,
        scan.best_below,
        scan.mapped,
        scan.dts,
        scan.n_days,
    )

    if args.json:
        _json.dump(
            {
                "target": f"{ecu}:{pid}",
                "n_captures": len(dts),
                "n_days": n_days,
                "span_days": round((dts[-1] - dts[0]).total_seconds() / 86400, 2),
                "payload_len": scan.payload_len,
                "min_bits": args.min_bits,
                "scoped": bool(scope_flags),
                "keep_unique": scope_is_keep_unique(lp.captures),
                "keep_changes": scope_is_keep_changes(lp.captures),
                "best_below_min_bits": (
                    _as_json(best_below, _expression(best_below), _mapped_by(best_below, mapped))
                    if best_below is not None and not groups
                    else None
                ),
                "max_width": args.counter_width,
                "counters": [
                    _as_json(rep, _expression(rep), _mapped_by(rep, mapped))
                    | {"subsumed": [_label(m) for m in members]}
                    for rep, members in groups
                ],
            },
            sys.stdout,
            indent=2,
            default=str,
        )
        print()
        return 0

    _print_counters(
        ecu,
        pid,
        groups,
        mapped,
        dts,
        n_days,
        args,
        best_below,
        notation,
        sub_bytes,
        bool(scope_flags),
        lp.captures,
    )
    return 0


@dataclass
class CounterScan:
    """The detection result for one PID — the reusable core behind the CLI view.

    Separates *finding* counters from *rendering* them so the corpus-wide sweep
    (``canair investigate --counters`` with no single PID) can call
    :func:`scan_counters` per PID and roll the results into one ranked summary,
    while the single-PID path renders the full per-window view.
    """

    groups: list[tuple[CounterCandidate, list[CounterCandidate]]]
    best_below: CounterCandidate | None
    mapped: dict[int, tuple[str, bool]]
    dts: list[datetime]
    n_days: int
    payload_len: int


def warn_scoped_counters(scope_flags: list[str]) -> str:
    """The stderr banner for a scoped ``--counters`` run (shared single/sweep)."""
    return (
        "investigate --counters: scope filter(s) "
        f"{', '.join(scope_flags)} active — a counter needs the full history to "
        "prove it only rises, so the bits score is likely understated. Prefer an "
        "unscoped run."
    )


def scan_counters(pid: str, lp: LoadedPid, args, params_def: dict) -> CounterScan | None:
    """Detect monotonic-counter windows on one PID (no rendering, no scope warning).

    Returns ``None`` when there are too few timed captures to test monotonicity.
    """
    dts, payloads = _payload_matrix(lp)
    if len(dts) < 2:
        return None

    # Columns keyed by ISO-TP offset, ascending — contiguous by construction, so
    # consecutive entries satisfy find_counters' adjacency contract.
    columns: list[tuple[object, list[int]]] = [
        (i, [p[i] for p in payloads]) for i in range(len(payloads[0]))
    ]
    ts = [d.timestamp() for d in dts]
    # Per-byte bit-flip rates — the boundary-gradient tie-break for cluster_counters
    # (a real multi-byte counter's low bits flip far more than its high bits).
    bit_flip = {key: bit_flip_rates(vals) for key, vals in columns}

    candidates = find_counters(
        columns,
        ts,
        session_gap_s=DEFAULT_SESSION_GAP_S,
        max_width=args.counter_width,
        min_bits=min(args.min_bits, SCAN_FLOOR_BITS),
    )
    # mapped_offsets is keyed by WiCAN offset; counters work in ISO-TP space.
    mapped_wican = mapped_offsets(params_def)
    mapped: dict[int, tuple[str, bool]] = {}
    for woff, info in mapped_wican.items():
        iso = wican_to_isotp(woff)
        if iso is not None:
            mapped[iso] = info

    groups = cluster_counters(candidates, bit_flip=bit_flip)
    if args.unmapped_only:
        # Hide only *settled* windows (every covering param verified). A window
        # mapped by an unverified guess is still open work, and monotonicity is
        # often what disproves the guess — suppressing it would hide the finding.
        groups = [(rep, members) for rep, members in groups if not _mapped_by(rep, mapped)[1]]
    # Sweeping at the floor (rather than at the user's threshold) costs nothing —
    # the threshold only filters — and lets an empty report name the value that
    # would actually surface something, instead of leaving the user to guess.
    below = [rep for rep, _m in groups if rep.bits < args.min_bits]
    groups = [(rep, members) for rep, members in groups if rep.bits >= args.min_bits]
    groups.sort(key=lambda g: -g[0].bits)
    # Same preference order as a cluster representative, so the named threshold
    # points at the window a re-run would actually lead with.
    best_below = min(
        below,
        key=lambda c: (not c.canonical, -c.bits, -c.width, c.msb_jump),
        default=None,
    )

    n_days = len({d.date() for d in dts})
    return CounterScan(
        groups=groups,
        best_below=best_below,
        mapped=mapped,
        dts=dts,
        n_days=n_days,
        payload_len=len(payloads[0]),
    )


def _print_counters(
    ecu,
    pid,
    groups,
    mapped,
    dts,
    n_days: int,
    args,
    best_below,
    notation: ByteNotation,
    sub_bytes: int,
    scoped: bool,
    captures,
) -> None:
    span_days = (dts[-1] - dts[0]).total_seconds() / 86400
    print()
    print(
        f"  {_BOLD}{_CYAN}{ecu} {pid}{_RESET} monotonic counters — "
        f"{len(dts)} captures over {n_days} day(s) / {span_days:.0f}d span"
        f"  {_DIM}(min-bits {args.min_bits:g}, width ≤{args.counter_width}){_RESET}"
    )
    # keep:changes rows are value-transitions, not fixed-rate samples — monotonicity
    # survives run-length dedup, but the step counts / n do change, so the caveat belongs.
    print_keep_banner(captures)
    if not groups:
        print()
        if best_below is not None:
            expr = _expression(best_below) or _display_label(best_below, notation, sub_bytes)
            print(
                f"  {_DIM}Nothing above {args.min_bits:g} bits. Best below it: "
                f"{_RESET}{expr}{_DIM} at {best_below.bits:.1f} bits "
                f"({_fmt(best_below.first)} → {_fmt(best_below.last)}).{_RESET}"
            )
            print(
                f"  {_DIM}Re-run with {_RESET}--min-bits {best_below.bits:g}{_DIM} to see it "
                f"— but treat it as a lead, not a finding: {best_below.bits:.0f} clean "
                f"rise(s){_RESET}"
            )
            print(f"  {_DIM}happen by chance about 1 in {2**best_below.bits:.0f} times.{_RESET}")
            if scoped:
                # The threshold above was computed from the FILTERED subset, so it
                # is only right for this scope — the full history could surface more.
                print(
                    f"  {_YELLOW}Note: computed from a scoped subset — re-run unscoped "
                    f"for the true threshold.{_RESET}"
                )
        else:
            print(
                f"  {_DIM}No counter-like window found. A counter needs a long enough "
                f"horizon to prove it only rises —{_RESET}"
            )
            print(
                f"  {_DIM}widen the scope (drop --state/--since) or grow the capture "
                f"set for this PID.{_RESET}"
            )
        print()
        return

    for kind in ("accumulator", "cycle", "timer"):
        sel = [(r, m) for r, m in groups if r.kind == kind]
        if not sel:
            continue
        title, blurb = _KIND_TITLES[kind]
        print()
        print(f"  {_BOLD}{title}{_RESET} {_DIM}— {blurb}{_RESET}")
        for rep, members in sel:
            _print_one(rep, members, mapped, notation, sub_bytes)
    print()


def _print_one(
    rep: CounterCandidate,
    members: list[CounterCandidate],
    mapped,
    notation: ByteNotation,
    sub_bytes: int,
) -> None:
    expr = _expression(rep) or "?"
    mapped_by, mapped_verified = _mapped_by(rep, mapped)
    if mapped_by is None:
        tag = f"{_YELLOW}UNMAPPED{_RESET}"
    elif mapped_verified:
        tag = f"{_DIM}[{mapped_by}]{_RESET}"
    else:
        tag = f"{_YELLOW}[{mapped_by}?]{_RESET}"  # mapped but unverified — still open
    print()
    print(
        f"    {_BOLD}{_display_label(rep, notation, sub_bytes):<12}{_RESET} "
        f"{_GREEN}{expr}{_RESET}  {tag}"
    )
    print(
        f"      {_fmt(rep.first)} → {_fmt(rep.last)}  (Δ{_fmt(rep.total_delta)})"
        f"   {_DIM}bits={rep.bits:.1f}  up={rep.n_up} down={rep.n_down}"
        f"  vary={rep.n_varying}/{rep.width}{_RESET}"
    )
    detail = (
        f"      {_DIM}med step {_fmt(rep.med_step)} · max step {_fmt(rep.max_step)}"
        f" · {rep.n_sessions} session(s)"
    )
    if rep.per_year is not None:
        detail += f" · ≈{_fmt(rep.per_year)}/year"
    print(detail + _RESET)
    if rep.kind == "cycle":
        print(
            f"      {_DIM}flat in {rep.flat_sessions}/{rep.n_sessions} sessions, "
            f"stepped across {rep.boundary_steps} boundary(ies){_RESET}"
        )
    if rep.kind == "timer" and rep.tick:
        print(
            f"      {_DIM}tick ≈ {rep.tick} (err {rep.tick_err:.0%}, "
            f"slope cv {rep.slope_cv:.0%}, resets to {rep.reset_frac:.1%}){_RESET}"
        )
        for fit in rep.sessions[:2]:
            when = datetime.fromtimestamp(fit.start_t).isoformat(sep=" ", timespec="seconds")
            print(
                f"        {_DIM}{when}  n={fit.n} dur={fit.duration_s:.0f}s "
                f"{_fmt(fit.lo)}→{_fmt(fit.hi)} slope={fit.slope:.4g} r={fit.r:.4f}{_RESET}"
            )
    if not rep.canonical:
        print(
            f"      {_DIM}note: window is padded (a constant zero high byte or a "
            f"constant low byte) — the real counter may be narrower{_RESET}"
        )
    elif rep.n_varying < rep.width:
        # Canonical only checks the OUTERMOST bytes, so a constant non-zero high
        # byte (or a constant interior one) still passes while inflating the
        # magnitude by 256 per byte — the printed first→last then reads as
        # millions where the informative range is a couple of counts.
        print(
            f"      {_DIM}note: only {rep.n_varying} of {rep.width} bytes vary — the "
            f"constant one(s) inflate the magnitude; read the varying byte(s) alone "
            f"for the real count{_RESET}"
        )
    if members:
        subsumed = ", ".join(_display_label(m, notation, sub_bytes) for m in members[:10])
        print(f"      {_DIM}subsumed: {subsumed}{_RESET}")
