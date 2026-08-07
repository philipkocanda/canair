"""``investigate uds`` — the default kind: everything about one diagnostic PID.

Bundles the manual ``coverage -> discriminate -> correlate -> hunt`` loop into one
ranked table over every varying data byte, plus ``--bits``, ``--events`` and the
probable-multi-byte-word section.
"""

from __future__ import annotations

import json as _json
import sys

from canlib.align import (
    discover_signal_specs,
    load_signal_captures,
    prepare_series,
)
from canlib.byteindex import mapped_bits, mapped_offsets
from canlib.capture_dates import resolve_scope_bounds
from canlib.commands._join import (
    fill_policy_from_args,
    fill_summaries,
    fill_summary_line,
)
from canlib.fill import forced_hold_warning
from canlib.keepmode import scope_is_keep_changes, scope_is_keep_unique
from canlib.triage import detect_words, triage_byte
from canlib.xanalysis import (
    build_bit_series,
    build_byte_series,
    build_param_series,
)

from .counters import run_counters
from .render import print_dwell, print_events, print_report
from .report import (
    _best_anchor,
    _ByteReport,
    _driver_r,
    _independence_score,
    _parse_bit_key,
    _state_f,
    _word_expr,
)

NAME = "investigate"


def run(args) -> int:
    since, until, err = resolve_scope_bounds(args)
    if err:
        print(f"error: {err}", file=sys.stderr)
        return 2

    fill = fill_policy_from_args(args)
    specs, single = _resolve_targets(args, since, until)
    if single is not None:
        return _run_single(single[0], single[1], args, since, until, fill)

    # Corpus/ECU sweep — no single PID resolved. The per-byte deep-dive is far too
    # verbose to repeat N times, so the sweep renders a ranked SUMMARY per PID
    # (and, for --counters, one ranked list of every counter found).
    from .sweep import run_sweep

    return run_sweep(specs, args, since, until, fill)


def _resolve_targets(args, since, until) -> tuple[list[tuple[str, str]], tuple[str, str] | None]:
    """Resolve the ECU/PID positionals into (specs, single).

    ``single`` is the one ``(ECU, PID)`` to render in full, or ``None`` for a
    sweep. Following the ``coverage`` precedent both positionals are optional:

      - ``ECU PID``  → that single PID (the classic deep-dive).
      - ``ECU`` / a QUERY (``"BMS:2101,2102"``) → every matching captured PID.
      - (nothing)    → every captured PID in the profile.

    A bare single ECU token is canonicalized (alias/case) before it becomes a
    query; a full QUERY is passed through to the shared mini-language parser. A
    query that resolves to exactly one PID is still rendered in full.
    """
    from canlib.ecus import canonical_ecu_name_safe

    if args.ecu and args.pid:
        ecu = canonical_ecu_name_safe(args.ecu).upper()
        spec = (ecu, args.pid.upper())
        return [spec], spec

    query = args.ecu
    if query and not any(ch in query for ch in ":, "):
        query = canonical_ecu_name_safe(query)
    specs = discover_signal_specs(
        query, since=since, until=until, state=args.state, label=args.label
    )
    if len(specs) == 1:
        return specs, specs[0]
    return specs, None


def _run_single(ecu: str, pid: str, args, since, until, fill) -> int:
    from canlib.pids import build_ecu_index, load_pids
    from canlib.xanalysis import byte_state_buckets as _byte_state_buckets

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

    forced = forced_hold_warning(lp.captures, fill)
    if forced:
        print(f"investigate: {forced}", file=sys.stderr)

    # Target byte series (min_distinct=2 so near-binary relay bytes count).
    target = build_byte_series(lp, min_distinct=2, fill=fill)

    # Which offsets/bits are already mapped by a defined param, and at what confidence.
    ecu_index = build_ecu_index(load_pids())
    params_def = ecu_index.get(ecu.upper(), {}).get("pids", {}).get(pid, {}).get("parameters", {})
    mapped = mapped_offsets(params_def)
    mapped_bit = mapped_bits(params_def)

    # State buckets per byte/bit (F score) — reuse decode's bucketer over a lite
    # all_results (only needs r["capture"]).
    all_results = [{"capture": c} for c in lp.captures]
    state_buckets = _byte_state_buckets(all_results, "state", include_bits=args.bits)

    # --events short-circuits to the edge/timeline view (no anchor correlation);
    # --dwell adds (or stands in for) the per-signal on-duration summary.
    if args.events or args.dwell:
        if args.events:
            print_events(ecu, pid, lp, mapped, mapped_bit, args, params_def)
        if args.dwell:
            print_dwell(ecu, pid, lp, mapped, mapped_bit, args)
        return 0

    # --counters asks a different question of the same captures ("which window only
    # ever rises?"), over multi-byte windows rather than single bytes, so it has its
    # own sweep and view rather than another column on the per-byte table.
    if args.counters:
        return run_counters(ecu, pid, lp, args, params_def)

    # Physical-band hits per starting offset (plausibility, needs no reference).
    # Computed after the --events short-circuit so it isn't wasted there.
    from canlib.grid_prompt import resolve_grid_region
    from canlib.physical_bands import resolve_physical_bands
    from canlib.profile import active
    from canlib.xanalysis import physical_scan

    bands = resolve_physical_bands(active().meta, grid_region=resolve_grid_region())
    physical_by_off = {
        h.offset: f"{h.scaling}·{h.expr} ≈ {h.band}" for h in physical_scan(lp, bands=bands)
    }

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
                    args.independent_of,
                    since=since,
                    until=until,
                    state=args.state,
                    label=args.label,
                    fill=fill,
                )
        except ValueError as e:
            flag = "--independent-of-file" if args.independent_of_file else "--independent-of"
            print(f"{flag} error: {e}", file=sys.stderr)
            return 1

    # Anchor signals: every param on the OTHER co-polled ECU/PIDs in scope.
    anchors: dict[str, list] = {}
    fills = fill_summaries([lp], fill, args.join_tol)
    other_specs = [
        s
        for s in discover_signal_specs(
            None, since=since, until=until, state=args.state, label=args.label
        )
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
            anchors.update(build_param_series(alp, aparams, fill=fill))
        # Anchors are the *joined* side, so a run-length anchor is exactly what
        # filling recovers: it can now reach every target instant instead of only
        # the few where it happened to be re-polled.
        fills.extend(fill_summaries(aloaded.values(), fill, args.join_tol))

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
                anchor=best.label if best else None,
                anchor_r=best.r if best else None,
                anchor_n=best.n if best else 0,
                slope=best.slope if best else None,
                intercept=best.intercept if best else None,
                unit_guess=best.unit_guess if best else None,
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
        for k, s in build_byte_series(lp, min_distinct=1, fill=fill).items()
    ]
    # Keep only pairs that render to a real adjacent word expression (drops any
    # non-adjacent pair rather than emit a misleading [Bhi:Blo] spanning a gap).
    word_candidates = [
        (w, expr) for w in detect_words(word_cols) if (expr := _word_expr(w)) is not None
    ]

    if args.bits:
        for key, series in build_bit_series(lp, fill=fill).items():
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
                    anchor=best.label if best else None,
                    anchor_r=best.r if best else None,
                    anchor_n=best.n if best else 0,
                    slope=best.slope if best else None,
                    intercept=best.intercept if best else None,
                    unit_guess=best.unit_guess if best else None,
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
                "fill": {
                    "mode": fill.mode,
                    "max_hold_s": fill.max_hold_s,
                    "signals": [f.as_json() for f in fills],
                },
                "independent_of": driver_label,
                "keep_unique": scope_is_keep_unique(lp.captures),
                "keep_changes": scope_is_keep_changes(lp.captures),
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

    print_report(
        ecu,
        pid,
        reports,
        args,
        lp,
        bool(anchors),
        driver_label=driver_label,
        words=word_candidates,
        fill_line=fill_summary_line(fills, fill),
    )
    return 0
