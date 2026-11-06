"""``hunt uds``: which byte of a diagnostic PID tracks a known signal."""

from __future__ import annotations

import json as _json
import sys

from canair.analysis.align import (
    load_signal_captures,
    longest_payload_len,
)
from canair.analysis.cross_signal import (
    hunt_byte,
    load_ref,
    ref_unit_for,
    reference_is_absolute_level,
    reference_is_bimodal,
    transform_ref,
)
from canair.analysis.fill import forced_hold_warning
from canair.cli.args.join import (
    fill_policy_from_args,
    fill_summaries,
    fill_summary_line,
)
from canair.cli.args.notation import resolve_notation
from canair.cli.args.scope import resolve_scope_bounds
from canair.cli.commands._analysis_warning import warn_analysis
from canair.expression.inspect_bytes import NO_EXPR
from canair.profile import active
from canair.render import ansi, messages
from canair.uds.layout import subfunction_bytes_for_pid
from canair.variants import effective_meta

from .physical import run_physical
from .promote import promote
from .render import hit_label


def run(args) -> int:
    from canair.analysis.physical.unit_guess import resolve_unit_candidates
    from canair.ecus.names import canonical_ecu_name_safe

    if not args.ecu or not args.pid:
        messages.error("ECU and PID are required (e.g. `hunt AAF 2181 --against …`)")
        return 2

    since, until, err = resolve_scope_bounds(args)
    if err:
        messages.error(f"{err}")
        return 2

    ecu = canonical_ecu_name_safe(args.ecu, profile=active())
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
            messages.warning(
                f"--physical ignores {', '.join(ignored)} "
                "(it scans value plausibility, not a reference correlation)"
            )
        return run_physical(args, ecu, pid, since, until)

    if args.control and args.control_file:
        messages.error("--control and --control-file are mutually exclusive")
        return 2

    fill = fill_policy_from_args(args)
    try:
        if args.against_file:
            from canair.analysis.align import load_reference_file

            ref_series, ref_label = load_reference_file(args.against_file)
        else:
            ref_series, ref_label = load_ref(
                args.against,
                since=since,
                until=until,
                state=args.state,
                label=args.label,
                fill=fill,
                profile=active(),
            )
    except ValueError as e:
        flag = "--against-file" if args.against_file else "--against"
        messages.error(f"{flag}: {e}")
        return 1

    if args.transform and args.transform != "raw":
        ref_series = transform_ref(ref_series, args.transform)
        ref_label = f"{args.transform}({ref_label})"

    if reference_is_bimodal([tp.value for tp in ref_series]):
        from canair.analysis.stats import categorical_method_nudge

        messages.warning(
            f"reference {ref_label!r} collapses into ~2 clusters "
            "(bimodal) \u2014 |r| then ranks cluster separation, not a signal match, so "
            "the top hits are unreliable. Use a scope with continuous variation "
            f"(a keep-all drive), not a bimodal regime flip.{categorical_method_nudge(args.method)} "
            "See docs/concepts/analysis-commands.md."
        )
    elif reference_is_absolute_level([tp.value for tp in ref_series]):
        messages.warning(
            f"reference {ref_label!r} looks like a slowly-varying "
            "absolute level (small swing on a large baseline) \u2014 Pearson |r| is then "
            "corrupted by cross-session DC offsets, so --against ranking is unreliable "
            "for it. Prefer `hunt --physical` (named bands) plus comparing per-state "
            "absolute readings to a known value. See docs/concepts/analysis-commands.md."
        )

    control_series = None
    if args.control or args.control_file:
        try:
            if args.control_file:
                from canair.analysis.align import load_reference_file

                control_series, control_label = load_reference_file(args.control_file)
            else:
                control_series, control_label = load_ref(
                    args.control,
                    since=since,
                    until=until,
                    state=args.state,
                    label=args.label,
                    fill=fill,
                    profile=active(),
                )
        except ValueError as e:
            flag = "--control-file" if args.control_file else "--control"
            messages.error(f"{flag}: {e}")
            return 1
        ref_label = f"{ref_label} · control {control_label}"

    loaded = load_signal_captures(
        [(ecu, pid)],
        since=since,
        until=until,
        state=args.state,
        label=args.label,
        one_revision="hunt",
        profile=active(),
    )
    lp = loaded[(ecu.upper(), pid)]
    if not lp.captures:
        messages.error(
            f"no timed captures for {ecu} {pid} in scope"
            + (f" ({lp.n_no_time} untimed skipped)" if lp.n_no_time else "")
        )
        return 1

    # Scope diagnostic BEFORE the sweep: every byte candidate shares the target's
    # frame timestamps, so the ref-vs-frames overlap is the exact ceiling on any
    # candidate's `n`. Without this, a non-overlapping scope reports "no byte
    # correlates" — indistinguishable from a real negative result.
    forced = forced_hold_warning(lp.captures, fill)
    if forced:
        warn_analysis(forced)
    fills = fill_summaries([lp], fill, args.join_tol)
    _warn_thin_reference_join(args, lp, ref_series, ref_label)

    hits = hunt_byte(
        lp,
        ref_series,
        tol_s=args.join_tol,
        min_n=args.min_n,
        top=args.top,
        method=args.method,
        all_interps=args.all_interps,
        control=control_series,
        ref_unit=None if args.against_file else ref_unit_for(args.against, profile=active()),
        candidates=resolve_unit_candidates(effective_meta(active())),
        per_session=args.per_session,
        session_gap_s=args.session_gap,
        fill=fill,
    )

    if args.promote:
        return promote(args.promote, ecu, pid, hits, ref_label)

    if args.json:
        _json.dump(
            {
                "target": f"{ecu}:{pid}",
                "reference": ref_label,
                "join_tol_s": args.join_tol,
                "fill": {
                    "mode": fill.mode,
                    "max_hold_s": fill.max_hold_s,
                    "signals": [f.as_json() for f in fills],
                },
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
        f"\n  {ansi.BOLD}Hunt {ecu} {pid} vs {ref_label}{ansi.RESET} "
        f"{ansi.DIM}(nearest-join ≤{args.join_tol:g}s){ansi.RESET}"
    )
    fill_line = fill_summary_line(fills, fill)
    if fill_line:
        print(f"  {ansi.DIM}{fill_line}{ansi.RESET}")
    for h in hits:
        color = ansi.GREEN if abs(h.r) >= 0.7 else ansi.YELLOW if abs(h.r) >= 0.3 else ansi.DIM
        unit = f"  {ansi.DIM}{h.unit_guess}{ansi.RESET}" if h.unit_guess else ""
        label = hit_label(
            h, notation, sub_bytes, longest_payload_len(getattr(lp, "captures", None))
        )
        print(
            f"    {color}r={h.r:+.3f}{ansi.RESET}  {ansi.BOLD}{label}{ansi.RESET} "
            f"{ansi.DIM}({h.interp}){ansi.RESET}  fit y={h.slope:.4f}·x{h.intercept:+.2f} "
            f"{ansi.DIM}resid={h.resid:.2f} n={h.n}{ansi.RESET}{unit}"
        )
    print()
    # A float reinterpretation has no WiCAN expression, so it can't be promoted or
    # written as a param — and reading integer byte runs as IEEE floats routinely
    # produces a series that correlates spuriously. Ranking is by |r| alone, so such
    # a hit can top the table (and crowd out expressible ones) with nothing saying
    # it's a dead end. Warn rather than reorder: a float read *can* be the real
    # signal on an ECU that genuinely transmits floats, so the user decides.
    n_noexpr = sum(1 for h in hits if h.expr == NO_EXPR)
    if hits and hits[0].expr == NO_EXPR:
        extra = f" ({n_noexpr} of the {len(hits)} shown)" if n_noexpr > 1 else ""
        messages.warning(
            f"the top hit is a float reinterpretation{extra} with no WiCAN "
            "expression, so it cannot be promoted or defined as a signal. Float reads of "
            "integer byte runs correlate spuriously — prefer the best expressible hit below "
            "it unless this ECU really transmits IEEE floats."
        )
    return 0


def _warn_thin_reference_join(args, lp, ref_series, ref_label: str) -> None:
    """Warn when the reference barely time-aligns onto the target's captures.

    Every byte interpretation of a PID is read from the same frames, so the
    reference-vs-frames nearest join bounds every candidate's realised ``n``. If
    that bound is 0 (or under ``--min-n``), the sweep can only come back empty —
    for a *scope/tolerance* reason, not because nothing correlates.
    """
    from canair.analysis.align import TimePoint, join_prepared, prepare_series, thin_join_warning

    frames = lp.timed_frames()
    if not frames or not ref_series:
        return
    target = prepare_series([TimePoint(dt, 0.0) for dt, _fr in frames])
    _xs, _ys, n = join_prepared(prepare_series(ref_series), target, tol_s=args.join_tol)
    msg = thin_join_warning(
        command="hunt",
        ref_label=ref_label,
        n_joined=n,
        n_candidates=len(ref_series),
        tol_s=args.join_tol,
        min_n=args.min_n,
    )
    if msg:
        warn_analysis(msg)
