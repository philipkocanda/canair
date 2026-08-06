"""``correlate uds`` — the default kind, over the diagnostic capture store.

The ranked cross-signal list and its variants (``--against``, ``--control``,
``--matrix``, ``--overlap``, ``--find-mirrors``, ``--lag-scan``), time-aligned
across every co-polled ECU/PID in scope.
"""

from __future__ import annotations

import json as _json
import sys

from canlib.align import (
    detrend_by_session,
    discover_signal_specs,
    join_prepared,
    prepare_series,
)
from canlib.capture_dates import resolve_scope_bounds
from canlib.commands._join import (
    fill_policy_from_args,
    fill_summary_line,
)
from canlib.corrmatrix import CLUSTER_THRESHOLD
from canlib.fill import FILL_HOLD, FORCED_HOLD_WARNING
from canlib.keepmode import CHANGES_BANNER
from canlib.notation import relabel_signal, resolve_notation
from canlib.xanalysis import (
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

from .calc import _apply_gate
from .promote import _promote_top_byte, _warn_thin_reference_join
from .render import (
    _color_r,
    _print_cross_mirrors,
    _print_overlap,
)
from .series import _fill_json, _gather_series, _scope_keep_flags

NAME = "correlate"

_BOLD = "\033[1m"
_DIM = "\033[2m"
_GREEN = "\033[92m"
_CYAN = "\033[96m"
_YELLOW = "\033[93m"
_RESET = "\033[0m"


def run(args) -> int:
    since, until, err = resolve_scope_bounds(args)
    if err:
        print(f"error: {err}", file=sys.stderr)
        return 2

    if args.against and args.against_file:
        print("error: --against and --against-file are mutually exclusive", file=sys.stderr)
        return 2

    notation = resolve_notation(args.notation)
    specs = discover_signal_specs(
        args.query, since=since, until=until, state=args.state, label=args.label
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
        print(f"correlate: {FORCED_HOLD_WARNING}", file=sys.stderr)
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
                f"⚠ {what} on keep:unique data is unreliable — stored-row time "
                f"gaps are dedup artifacts, not real sampling intervals."
            )
        if keep_changes:
            caveats.append(f"⚠ {CHANGES_BANNER}.")
            if transform_caveat:
                caveats.append(
                    f"⚠ {what} on keep:changes data is unreliable — stored rows are "
                    f"value-transitions, not fixed-rate samples."
                )
        if caveats:
            for line in caveats:
                print(f"  {_YELLOW}{line}{_RESET}")
            print()

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
        print("No time-aligned signals found in scope.", file=sys.stderr)
        return 1

    fill_line = fill_summary_line(fills, fill)
    if fill_line and not args.json:
        print(f"  {_CYAN}{fill_line}{_RESET}\n")

    per_session = getattr(args, "per_session", False)
    if per_session:
        series = {k: detrend_by_session(v, args.session_gap) for k, v in series.items()}
        print(
            "correlate: --per-session active — each recording's DC baseline removed; "
            "ranking in-session variation, not absolute level.",
            file=sys.stderr,
        )

    # --against / --against-file: rank every signal vs one reference.
    if args.against or args.against_file:
        try:
            if args.against_file:
                from canlib.align import load_reference_file

                ref_series, ref_label = load_reference_file(args.against_file)
            else:
                ref_series, ref_label = load_ref(
                    args.against,
                    since=since,
                    until=until,
                    state=args.state,
                    label=args.label,
                    fill=fill,
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

        if reference_is_bimodal([tp.value for tp in ref_series]):
            from canlib.stats import categorical_method_nudge

            print(
                f"correlate: warning: reference {ref_label!r} collapses into ~2 clusters "
                "(bimodal) \u2014 |r| then ranks cluster separation, not a real relationship, "
                "so --against results are unreliable. Prefer a scope with continuous "
                f"variation.{categorical_method_nudge(args.method)} "
                "See docs/concepts/analysis-commands.md.",
                file=sys.stderr,
            )
        elif reference_is_absolute_level([tp.value for tp in ref_series]):
            print(
                f"correlate: warning: reference {ref_label!r} looks like a slowly-varying "
                "absolute level (small swing on a large baseline) \u2014 Pearson |r| is then "
                "corrupted by cross-session DC offsets, so --against results are unreliable "
                "for it. Prefer `hunt --physical` (named bands) plus comparing per-state "
                "absolute readings to a known value. See docs/concepts/analysis-commands.md.",
                file=sys.stderr,
            )

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
                        args.control,
                        since=since,
                        until=until,
                        state=args.state,
                        label=args.label,
                        fill=fill,
                    )
            except ValueError as e:
                flag = "--control-file" if args.control_file else "--control"
                print(f"{flag} error: {e}", file=sys.stderr)
                return 1
            ref_label = f"{ref_label} · control {control_label}"

        if per_session:
            ref_series = detrend_by_session(ref_series, args.session_gap)
            if control_series is not None:
                control_series = detrend_by_session(control_series, args.session_gap)

        rows = []
        best_n = 0  # best realised overlap across the sweep (pre-min_n) — see below
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
                if hit is None or abs(hit.r) < args.min_r or hit.n < args.min_n:
                    continue
                rows.append((name, hit.r, hit.n, hit.lag_seconds))
            elif control_series is not None:
                from canlib.align import join_nearest_triple
                from canlib.stats import partial_correlation

                xs, ys, zs, n = join_nearest_triple(
                    ref_series, s, control_series, tol_s=args.join_tol
                )
                best_n = max(best_n, n)
                if n < args.min_n:
                    continue
                r = partial_correlation(xs, ys, zs, args.method)
                if r is None or abs(r) < args.min_r:
                    continue
                rows.append((name, r, n, None))
            else:
                xs, ys, n = join_prepared(ref_prepared, prepare_series(s), tol_s=args.join_tol)
                best_n = max(best_n, n)
                if n < args.min_n:
                    continue
                r = correlation(xs, ys, args.method)
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
                ref_unit=None if args.against_file else ref_unit_for(args.against),
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
            f"\n  {_BOLD}vs {ref_label}{_RESET} "
            f"{_DIM}(nearest-join ≤{args.join_tol:g}s, ref {len(ref_series)} samples{lag_hdr}){_RESET}"
        )
        for name, r, n, lag in rows:
            lag_str = f"  {_DIM}lag={lag:+.1f}s{_RESET}" if lag is not None else ""
            print(
                f"    {_color_r(r)}  {relabel_signal(name, notation, payload_lens=plens)}  "
                f"{_DIM}n={n}{_RESET}{lag_str}"
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
        shown_members = [relabel_signal(m, notation, payload_lens=plens) for m in members[:4]]
        shown = ", ".join(shown_members) + (
            f", +{len(members) - 4} more" if len(members) > 4 else ""
        )
        print(
            f"    {_GREEN}≈ cluster{_RESET} {_DIM}(|r|≥{CLUSTER_THRESHOLD:g}, "
            f"{len(members)} signals){_RESET}  {shown}"
        )
    for h in remaining:
        print(
            f"    {_color_r(h.r)}  {relabel_signal(h.a, notation, payload_lens=plens)}  "
            f"{_DIM}⟷{_RESET}  {relabel_signal(h.b, notation, payload_lens=plens)}  "
            f"{_DIM}n={h.n}{_RESET}"
        )
    print()
    return 0
