"""``correlate uds`` — the default kind, over the diagnostic capture store.

The ranked cross-signal list and its variants (``--against``, ``--control``,
``--matrix``, ``--overlap``, ``--find-mirrors``, ``--lag-scan``), time-aligned
across every co-polled ECU/PID in scope.
"""

from __future__ import annotations

import json as _json
import sys

from canair.analysis.align import (
    detrend_by_session,
    discover_signal_specs,
    join_prepared,
    prepare_series,
)
from canair.analysis.corrmatrix import CLUSTER_THRESHOLD
from canair.analysis.cross_signal import (
    colinear_clusters,
    correlate_matrix,
    correlation,
    lag_scan,
    load_ref,
    ref_unit_for,
    reference_is_absolute_level,
    reference_is_bimodal,
    transform_ref,
)
from canair.analysis.fill import FILL_HOLD, FORCED_HOLD_WARNING
from canair.captures.keepmode import CHANGES_BANNER
from canair.cli.args.join import fill_policy_from_args, fill_summary_line
from canair.cli.args.notation import resolve_notation
from canair.cli.args.scope import resolve_scope_bounds
from canair.cli.commands._analysis_warning import warn_analysis
from canair.cli.profile_context import active
from canair.expression.notation import relabel_signal
from canair.render import ansi, messages

from .calc import _apply_gate
from .promote import _promote_top_byte, _warn_thin_reference_join
from .render import (
    _color_r,
    _print_cross_mirrors,
    _print_overlap,
    weak_correlation_hint,
)
from .series import _fill_json, _gather_series, _scope_keep_flags

NAME = "correlate"


def run(args) -> int:
    since, until, err = resolve_scope_bounds(args)
    if err:
        messages.error(f"{err}")
        return 2

    if args.against and args.against_file:
        messages.error("--against and --against-file are mutually exclusive")
        return 2

    notation = resolve_notation(args.notation)
    specs = discover_signal_specs(
        args.query, since=since, until=until, state=args.state, label=args.label, profile=active()
    )

    if args.overlap:
        return _print_overlap(
            specs, since, until, args.state, args.label, args.join_tol, args.min_n, args.json
        )

    if args.find_mirrors:
        return _print_cross_mirrors(specs, since, until, args, notation)

    keep_unique, keep_changes = _scope_keep_flags(specs, since, until, args.state, args.label)
    fill = fill_policy_from_args(args)
    if keep_unique and fill.mode == FILL_HOLD:
        warn_analysis(FORCED_HOLD_WARNING)
    if not args.json:
        transform_caveat = args.transform in ("delta", "cumsum") or args.lag_scan
        what = (
            f"--transform {args.transform}"
            if args.transform in ("delta", "cumsum")
            else "--lag-scan"
        )
        caveats: list[str] = []
        if keep_unique and transform_caveat:
            caveats.append(
                f"{what} on keep:unique data is unreliable — stored-row time "
                f"gaps are dedup artifacts, not real sampling intervals."
            )
        if keep_changes:
            caveats.append(f"{CHANGES_BANNER}.")
            if transform_caveat:
                caveats.append(
                    f"{what} on keep:changes data is unreliable — stored rows are "
                    f"value-transitions, not fixed-rate samples."
                )
        if caveats:
            for line in caveats:
                messages.warning(line)

    series, plens, fills = _gather_series(
        specs,
        since,
        until,
        args.state,
        args.label,
        args.bytes,
        args.bits,
        fill,
        args.join_tol,
    )
    if not series:
        messages.error("no time-aligned signals found in scope")
        return 1

    fill_line = fill_summary_line(fills, fill)
    if fill_line and not args.json:
        print(f"  {ansi.DIM}{fill_line}{ansi.RESET}\n")

    per_session = getattr(args, "per_session", False)
    if per_session:
        series = {k: detrend_by_session(v, args.session_gap) for k, v in series.items()}
        messages.note(
            "--per-session active — each recording's DC baseline removed; "
            "ranking in-session variation, not absolute level"
        )

    # --against / --against-file: rank every signal vs one reference.
    if args.against or args.against_file:
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
                messages.error(f"--gate: {e}")
                return 1
            ref_label = f"{ref_label} [gate: {args.gate.strip()}]"
            if not ref_series:
                messages.error(f"--gate '{args.gate.strip()}' left no reference points in scope")
                return 1

        if reference_is_bimodal([tp.value for tp in ref_series]):
            from canair.analysis.stats import categorical_method_nudge

            messages.warning(
                f"reference {ref_label!r} collapses into ~2 clusters "
                "(bimodal) \u2014 |r| then ranks cluster separation, not a real relationship, "
                "so --against results are unreliable. Prefer a scope with continuous "
                f"variation.{categorical_method_nudge(args.method)} "
                "See docs/concepts/analysis-commands.md."
            )
        elif reference_is_absolute_level([tp.value for tp in ref_series]):
            messages.warning(
                f"reference {ref_label!r} looks like a slowly-varying "
                "absolute level (small swing on a large baseline) \u2014 Pearson |r| is then "
                "corrupted by cross-session DC offsets, so --against results are unreliable "
                "for it. Prefer `hunt --physical` (named bands) plus comparing per-state "
                "absolute readings to a known value. See docs/concepts/analysis-commands.md."
            )

        # --control: rank by partial correlation with a nuisance signal removed.
        control_series = None
        if args.control and args.control_file:
            messages.error("--control and --control-file are mutually exclusive")
            return 2
        if args.control or args.control_file:
            if args.method in ("cramers_v", "mutual_info"):
                messages.error(
                    "--control (partial correlation) is undefined for a "
                    "categorical --method (cramers_v/mutual_info)"
                )
                return 2
            if args.lag_scan:
                messages.error("--control cannot be combined with --lag-scan")
                return 2
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

        if per_session:
            ref_series = detrend_by_session(ref_series, args.session_gap)
            if control_series is not None:
                control_series = detrend_by_session(control_series, args.session_gap)

        rows = []
        best_n = 0  # best realised overlap across the sweep (pre-min_n) — see below
        # Strongest correlation that cleared min_n but not min_r, so an empty
        # report can name the --min-r that WOULD surface something (data-derived).
        best_below_r = 0.0
        best_below_label: str | None = None

        def _note_below(name: str, r: float | None, n: int) -> None:
            nonlocal best_below_r, best_below_label
            if r is not None and n >= args.min_n and abs(r) > best_below_r:
                best_below_r, best_below_label = abs(r), name

        ref_prepared = prepare_series(ref_series)
        for name, s in series.items():
            if not args.include_self and name == args.against:
                continue  # the reference vs itself — trivial r=1.0
            if args.lag_scan:
                hit = lag_scan(
                    ref_series, s, tol_s=args.join_tol, max_lag=args.lag_scan, method=args.method
                )
                if hit is not None:
                    best_n = max(best_n, hit.n)
                    _note_below(name, hit.r, hit.n)
                if hit is None or abs(hit.r) < args.min_r or hit.n < args.min_n:
                    continue
                rows.append((name, hit.r, hit.n, hit.lag_seconds))
            elif control_series is not None:
                from canair.analysis.align import join_nearest_triple
                from canair.analysis.stats import partial_correlation

                xs, ys, zs, n = join_nearest_triple(
                    ref_series, s, control_series, tol_s=args.join_tol
                )
                best_n = max(best_n, n)
                if n < args.min_n:
                    continue
                r = partial_correlation(xs, ys, zs, args.method)
                _note_below(name, r, n)
                if r is None or abs(r) < args.min_r:
                    continue
                rows.append((name, r, n, None))
            else:
                xs, ys, n = join_prepared(ref_prepared, prepare_series(s), tol_s=args.join_tol)
                best_n = max(best_n, n)
                if n < args.min_n:
                    continue
                r = correlation(xs, ys, args.method)
                _note_below(name, r, n)
                if r is None or abs(r) < args.min_r:
                    continue
                rows.append((name, r, n, None))
        # A reference whose scope doesn't overlap the candidates drops every one of
        # them at the min_n gate, which otherwise looks identical to "nothing
        # correlates". Report the tolerance/scope cause instead.
        _warn_thin_reference_join("correlate", ref_label, best_n, len(ref_series), args)
        rows.sort(key=lambda t: -abs(t[1]))
        if args.promote:
            return _promote_top_byte(
                args.promote,
                [(n, r, nn) for n, r, nn, _ in rows],
                series,
                ref_series,
                ref_label,
                args.join_tol,
                ref_unit=None
                if args.against_file
                else ref_unit_for(args.against, profile=active()),
            )
        rows = rows[: args.top]
        if args.json:
            _json.dump(
                {
                    "reference": ref_label,
                    "method": args.method,
                    "join_tol_s": args.join_tol,
                    "fill": _fill_json(fill, fills),
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
            f"\n  {ansi.BOLD}vs {ref_label}{ansi.RESET} "
            f"{ansi.DIM}(nearest-join ≤{args.join_tol:g}s, ref {len(ref_series)} samples{lag_hdr}){ansi.RESET}"
        )
        for name, r, n, lag in rows:
            lag_str = f"  {ansi.DIM}lag={lag:+.1f}s{ansi.RESET}" if lag is not None else ""
            print(
                f"    {_color_r(r)}  {relabel_signal(name, notation, payload_lens=plens)}  "
                f"{ansi.DIM}n={n}{ansi.RESET}{lag_str}"
            )
        if not rows:
            hint = weak_correlation_hint(
                best_below_r or None,
                relabel_signal(best_below_label, notation, payload_lens=plens)
                if best_below_label
                else None,
                args.min_r,
            )
            if hint:
                messages.hint(hint)
            else:
                print(f"    {ansi.DIM}no signal cleared the thresholds.{ansi.RESET}")
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
                "fill": _fill_json(fill, fills),
                "hits": [
                    {"a": h.a, "b": h.b, "r": h.r, "n": h.n, "n_filled": h.n_filled}
                    for h in hits[: args.top]
                ],
            },
            sys.stdout,
            indent=2,
        )
        print()
        return 0

    if not hits:
        print(f"No cross-signal correlations with |r| ≥ {args.min_r} (n ≥ {args.min_n}).")
        # Name the strongest sub-threshold pair (data-derived): re-sweep at min_r=0
        # — only on the empty path, so the extra pass is paid at most once.
        below = correlate_matrix(
            series,
            tol_s=args.join_tol,
            min_r=0.0,
            min_n=args.min_n,
            include_intra=args.include_intra,
            method=args.method,
        )
        if below:
            top = below[0]
            label = (
                f"{relabel_signal(top.a, notation, payload_lens=plens)} ~ "
                f"{relabel_signal(top.b, notation, payload_lens=plens)}"
            )
            hint = weak_correlation_hint(top.r, label, args.min_r)
            if hint:
                messages.hint(hint)
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
        f"\n  {ansi.BOLD}Cross-signal correlations{ansi.RESET} "
        f"{ansi.DIM}({len(series)} signals, |r|≥{args.min_r}, n≥{args.min_n}, "
        f"≤{args.join_tol:g}s){ansi.RESET}"
    )
    for c in sorted(clusters, key=len, reverse=True):
        members = sorted(c)
        shown_members = [relabel_signal(m, notation, payload_lens=plens) for m in members[:4]]
        shown = ", ".join(shown_members) + (
            f", +{len(members) - 4} more" if len(members) > 4 else ""
        )
        print(
            f"    {ansi.BOLD}≈ cluster{ansi.RESET} {ansi.DIM}(|r|≥{CLUSTER_THRESHOLD:g}, "
            f"{len(members)} signals){ansi.RESET}  {shown}"
        )
    for h in remaining:
        print(
            f"    {_color_r(h.r)}  {relabel_signal(h.a, notation, payload_lens=plens)}  "
            f"{ansi.DIM}⟷{ansi.RESET}  {relabel_signal(h.b, notation, payload_lens=plens)}  "
            f"{ansi.DIM}n={h.n}{ansi.RESET}"
        )
    print()
    return 0
