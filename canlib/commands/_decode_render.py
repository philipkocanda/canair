#!/usr/bin/env python3
"""Presentation layer for ``canair decode`` (extracted from decode.py).

Pure rendering: turns decode's ``all_results`` (and the series helpers in
``_decode_calc``) into the terminal tables/JSON/CSV the command prints. Kept
separate so decode.py stays argparse + orchestration and this module has no
import-time dependency back on decode.
"""

from __future__ import annotations

import json
import sys
from typing import TYPE_CHECKING

from canlib.commands._decode_calc import (
    _local_series,
    _paired,
    _paired_timed,
    _series,
    _transform_series,
    find_mirrors,
)
from canlib.commands._decode_plot import apply_transform
from canlib.notation import ByteNotation, relabel_signal
from canlib.states import join_states as _join_states
from canlib.stats import compute_stats
from canlib.stats import correlation as _correlation
from canlib.stats import fmt_num as _fmt_num
from canlib.xanalysis import byte_state_buckets as _byte_state_buckets
from canlib.xanalysis import discriminability as _discriminability

if TYPE_CHECKING:
    from canlib.align import TimePoint

# ANSI colors — kept local (not imported from decode) so this module has no
# import-time dependency on decode, which imports the renderers back.
_BOLD = "\033[1m"
_DIM = "\033[2m"
_GREEN = "\033[92m"
_YELLOW = "\033[93m"
_RED = "\033[91m"
_CYAN = "\033[96m"
_RESET = "\033[0m"


def format_value(v: float | None, unit: str) -> str:
    """Format a decoded value with unit."""
    if v is None:
        return "ERROR"
    if v == int(v):
        return f"{int(v)}{unit}"
    return f"{v:.2f}{unit}"


def check_range(value: float | None, param_result: dict) -> str | None:
    """Return warning if value is outside min/max range."""
    if value is None:
        return None
    mn = param_result.get("min")
    mx = param_result.get("max")
    try:
        if mn is not None and value < float(mn):
            return f"< min({mn})"
        if mx is not None and value > float(mx):
            return f"> max({mx})"
    except (ValueError, TypeError):
        pass
    return None


def scope_banner(since, until, state, label, first, last) -> str:
    """Human-readable summary of active scope filters (empty when none active)."""
    parts = []
    if since or until:
        lo = since.isoformat() if since else "earliest"
        hi = until.isoformat() if until else "latest"
        parts.append(f"{lo} .. {hi}")
    if state:
        parts.append(f"state~'{state}'")
    if label:
        parts.append(f"label~'{label}'")
    if first is not None:
        parts.append(f"first {first}")
    if last is not None:
        parts.append(f"last {last}")
    return "  ·  ".join(parts)


def _compact_cell(v: float | None) -> str:
    """Format one decoded value for a compact column (no unit; units go in header)."""
    if v is None:
        return "ERR"
    if v == int(v):
        return str(int(v))
    return f"{v:.2f}"


def print_compact(
    all_results: list[dict],
    param_names: list[str],
    parameters: dict,
    candidate_names: set[str],
    changes_only: bool = False,
) -> None:
    """One row per capture as an aligned table: header once, then time + values.

    Repetition is stripped compared with the old ``name=value name=value`` form:
      * parameter names print once in a header row (with units), not per cell;
      * a ``[state]`` divider prints only when the session state *changes*;
      * the date is dropped from each row when every capture is the same day
        (time-only rows), and printed inline only when the day rolls over;
      * values are right-aligned in fixed-width columns so a column reads as a
        trend. ``changes_only`` additionally drops rows identical (across all
        shown params) to the previous printed row — collapsing stationary runs.
    """
    # Only params that actually appear in the decoded results, in definition order.
    present = [n for n in param_names if any(n in r["decoded"] for r in all_results)]
    if not present:
        print(f"  {_DIM}(no decodable parameters in scope){_RESET}\n")
        return

    # Column widths: header name/unit vs the widest formatted value in the column.
    units = {n: parameters.get(n, {}).get("unit", "") for n in present}
    headers = {n: (f"{n}[{units[n]}]" if units[n] else n) for n in present}
    widths = {}
    for n in present:
        vals = [
            _compact_cell(r["decoded"].get(n, {}).get("value"))
            for r in all_results
            if n in r["decoded"]
        ]
        widths[n] = max([len(headers[n]), *(len(v) for v in vals)] or [len(headers[n])])

    # Are all captures the same date? If so, rows show time only.
    dates = {r["capture"].get("date", "") for r in all_results}
    single_day = len(dates) <= 1
    day = next(iter(dates)) if single_day else ""
    ts_w = max(
        (len((r["capture"].get("time") or r["capture"].get("date") or "")) for r in all_results),
        default=8,
    )

    def _colored_header(n: str) -> str:
        color = (
            _CYAN
            if n in candidate_names
            else (_GREEN if parameters.get(n, {}).get("verified") else _YELLOW)
        )
        return f"{color}{headers[n]:>{widths[n]}}{_RESET}"

    if single_day and day:
        print(f"  {_DIM}date {day}{_RESET}")
    header_cells = "  ".join(_colored_header(n) for n in present)
    print(f"  {_DIM}{'time':<{ts_w}}{_RESET}  {header_cells}")

    prev_state = None
    prev_row_vals = None
    cur_date = None
    for r in all_results:
        cap = r["capture"]
        state = _join_states(cap.get("vehicle_states"))
        if state != prev_state:
            label = state if state else "(no state)"
            print(f"  {_DIM}── [{label}] ─────{_RESET}")
            prev_state = state
            prev_row_vals = None  # force first row of a new state to print

        # Row timestamp: time within a single day; else full date+time.
        if single_day:
            ts = cap.get("time") or cap.get("date") or ""
        else:
            d = cap.get("date", "")
            t = cap.get("time", "")
            if d != cur_date:
                print(f"  {_DIM}date {d}{_RESET}")
                cur_date = d
            ts = t or d

        if r.get("error"):
            print(f"  {_DIM}{ts:<{ts_w}}{_RESET}  {_RED}{r['error']}{_RESET}")
            prev_row_vals = None
            continue

        raw_vals = tuple(r["decoded"].get(n, {}).get("value") for n in present)
        if changes_only and raw_vals == prev_row_vals:
            continue
        prev_row_vals = raw_vals

        cells = []
        for n in present:
            d = r["decoded"].get(n)
            cell = _compact_cell(d["value"]) if d else ""
            cells.append(f"{cell:>{widths[n]}}")
        print(f"  {_DIM}{ts:<{ts_w}}{_RESET}  {'  '.join(cells)}")
    print()


def print_value_ranges(
    all_results: list[dict],
    param_names: list[str],
    parameters: dict,
    candidate_names: set[str],
) -> None:
    """Print each parameter's decoded value range across all captures.

    This is decode.py's headline (default) view: parameter/value-centric, for
    validating expressions — distinct from query-captures.py's payload/byte-diff
    view. Params that only ever errored are surfaced with their error message so
    a bad expression (e.g. a --try candidate) is never silently hidden.
    """
    name_w = max((len(n) for n in param_names), default=10)
    for name in param_names:
        is_cand = name in candidate_names
        verified = parameters[name].get("verified", False)
        mark = (
            f"{_CYAN}»{_RESET}"
            if is_cand
            else f"{_GREEN}✓{_RESET}"
            if verified
            else f"{_YELLOW}?{_RESET}"
        )
        try_tag = f"  {_CYAN}(try){_RESET}" if is_cand else ""
        unit = parameters[name].get("unit", "")

        values = [
            r["decoded"][name]["value"]
            for r in all_results
            if name in r["decoded"] and r["decoded"][name].get("value") is not None
        ]
        ptype = (parameters[name].get("type") or "numeric").lower()

        # Typed params whose display doesn't depend on a numeric raw (ascii/date/
        # struct): render distinct decoded labels directly.
        if ptype in ("ascii", "date", "struct"):
            seen_a: list[str] = []
            for r in all_results:
                d = r["decoded"].get(name)
                if d and d.get("display") is not None and d["display"] not in seen_a:
                    seen_a.append(d["display"])
            if seen_a:
                shown_a = "  ".join(seen_a[:8]) + ("  …" if len(seen_a) > 8 else "")
                tag_a = (
                    f"{_DIM}(constant){_RESET}"
                    if len(seen_a) == 1
                    else f"{_DIM}({len(seen_a)} values){_RESET}"
                )
                print(f"    {mark} {name:<{name_w}}  {shown_a}  {tag_a}{try_tag}")
                continue

        if not values:
            err = next(
                (
                    r["decoded"][name]["error"]
                    for r in all_results
                    if name in r["decoded"] and r["decoded"][name].get("error")
                ),
                None,
            )
            msg = f"{_RED}ERROR: {err}{_RESET}" if err else f"{_DIM}no value{_RESET}"
            print(f"    {mark} {name:<{name_w}}  {msg}{try_tag}")
            continue

        mn, mx = min(values), max(values)
        mm = {"min": parameters[name].get("min"), "max": parameters[name].get("max")}
        warn = check_range(mn, mm) or check_range(mx, mm)
        warn_str = f"  {_RED}⚠ {warn}{_RESET}" if warn else ""

        if ptype in ("enum", "bitmask", "bcd"):
            # Categorical/typed: show the distinct decoded labels, not a numeric
            # range (min-max of the raw byte is meaningless for a mode/flag set).
            seen: list[str] = []
            for r in all_results:
                d = r["decoded"].get(name)
                if d and d.get("display") is not None and d["display"] not in seen:
                    seen.append(d["display"])
            shown = "  ".join(seen[:8]) + ("  …" if len(seen) > 8 else "")
            tag = (
                f"{_DIM}(constant){_RESET}"
                if len(seen) == 1
                else f"{_DIM}({len(seen)} values){_RESET}"
            )
            print(f"    {mark} {name:<{name_w}}  {shown}  {tag}{try_tag}{warn_str}")
            continue

        if mn == mx:
            print(
                f"    {mark} {name:<{name_w}}  {format_value(mn, unit):>14}  "
                f"{_DIM}(constant){_RESET}{try_tag}{warn_str}"
            )
        else:
            print(
                f"    {mark} {name:<{name_w}}  {format_value(mn, unit):>14} — "
                f"{format_value(mx, unit)}{try_tag}{warn_str}"
            )
    print()


def _mark_for(name: str, parameters: dict, candidate_names: set[str]) -> str:
    if name in candidate_names:
        return f"{_CYAN}»{_RESET}"
    verified = parameters.get(name, {}).get("verified", False)
    return f"{_GREEN}✓{_RESET}" if verified else f"{_YELLOW}?{_RESET}"


def print_stats_table(
    all_results: list[dict], param_names: list[str], parameters: dict, candidate_names: set[str]
) -> None:
    """Per-parameter descriptive statistics (n, distinct, min/max, mean, median, stdev).

    Enum-like parameters (few distinct values) also list value -> count, which
    helps classify a byte as a flag/enum vs a continuous signal during RE.
    """
    for name in param_names:
        mark = _mark_for(name, parameters, candidate_names)
        try_tag = f" {_CYAN}(try){_RESET}" if name in candidate_names else ""
        values = _series(all_results, name)
        if not values:
            print(f"    {mark} {name}{try_tag}: {_DIM}no value{_RESET}")
            continue
        s = compute_stats(values)
        unit = parameters[name].get("unit", "")
        print(
            f"    {mark} {_BOLD}{name}{_RESET}{try_tag} {_DIM}[{unit}]{_RESET}  "
            f"n={s['n']} distinct={s['distinct']}  "
            f"min={_fmt_num(s['min'])} max={_fmt_num(s['max'])} "
            f"mean={_fmt_num(s['mean'])} median={_fmt_num(s['median'])} "
            f"stdev={_fmt_num(s['stdev'])}"
        )
        if 1 < s["distinct"] <= 8:
            counts = {}
            for v in values:
                counts[v] = counts.get(v, 0) + 1
            enum = "  ".join(f"{_fmt_num(v)}(n={counts[v]})" for v in s["values"])
            print(f"        {_DIM}values: {enum}{_RESET}")
        elif s["distinct"] == 1:
            print(f"        {_DIM}(constant){_RESET}")
    print()


def print_stats_grouped(
    all_results: list[dict],
    param_names: list[str],
    parameters: dict,
    candidate_names: set[str],
    field: str,
) -> None:
    """Per-group descriptive statistics: split captures by session ``field`` first.

    Serves the drive-analysis workflow — e.g. ``--stats --group-by state`` yields
    min/max/mean per drive segment (``driving MT->KW`` vs ``Driving KW->Home``)
    in one shot instead of pooling every capture together. Groups are printed in
    first-appearance order so they follow the chronological session order.
    """
    groups: dict[str, list[dict]] = {}
    for r in all_results:
        cap = r["capture"]
        if field in ("state", "vehicle_states"):
            key = _join_states(cap.get("vehicle_states")) or "(no state)"
        else:
            key = str(cap.get(field, "")) or "(no state)"
        groups.setdefault(key, []).append(r)

    for gi, (key, rows) in enumerate(groups.items()):
        if gi:
            print()
        print(f"  {_BOLD}[{key}]{_RESET} {_DIM}— {len(rows)} captures{_RESET}")
        print_stats_table(rows, param_names, parameters, candidate_names)


def print_discriminate(
    all_results: list[dict],
    param_names: list[str],
    parameters: dict,
    candidate_names: set[str],
    field: str,
    *,
    include_bytes: bool = False,
    include_bits: bool = False,
    notation: ByteNotation = ByteNotation.WICAN,
    sub_bytes: int = 1,
) -> None:
    """Rank params (and optionally raw bytes/bits) by how cleanly they separate
    across session ``field`` groups.

    The confirmation lever for state-dependent signals (thermal/mode/relay) that
    a driving-anchor correlation misses — e.g. MCU inverter temp reads distinctly
    across charging/ready/driving. Uses an F-like between/within variance ratio.

    With ``include_bytes`` (``--bytes``) every varying non-PCI raw byte is ranked
    alongside the params; with ``include_bits`` (``--bits``) so is every varying
    bit (``Bn:k``) — finding a state-dependent byte/bit without a ``--try``.
    """
    buckets: dict[str, dict[str, list[float]]] = {name: {} for name in param_names}
    # Parallel categorical view: for typed enum/bitmask params, collect the
    # nominal category per capture alongside its state, so we can score them with
    # Cramér's V (F assumes interval scale — invalid for a mode/flag set).
    cat_pairs: dict[str, tuple[list, list]] = {}
    for r in all_results:
        key = _join_states(r["capture"].get("vehicle_states")) or "(no state)"
        for name in param_names:
            d = r["decoded"].get(name, {})
            v = d.get("value")
            if v is not None:
                buckets[name].setdefault(key, []).append(v)
            cat = d.get("category")
            if cat is not None:
                xs, ys = cat_pairs.setdefault(name, ([], []))
                xs.append(cat)
                ys.append(key)

    byte_names: set[str] = set()
    if include_bytes or include_bits:
        byte_buckets = _byte_state_buckets(all_results, field, include_bits=include_bits)
        byte_names = set(byte_buckets)
        buckets.update(byte_buckets)

    rows = []
    for name in list(buckets):
        score = _discriminability(buckets[name])
        rows.append((name, score, buckets[name]))
    rows.sort(key=lambda t: (t[1] is None, -(t[1] if t[1] is not None else 0)))

    hdr_extra = ""
    if include_bytes and include_bits:
        hdr_extra = " (params + bytes + bits)"
    elif include_bytes:
        hdr_extra = " (params + bytes)"
    elif include_bits:
        hdr_extra = " (params + bits)"
    print(
        f"  {_BOLD}Discriminability by {field}{hdr_extra}{_RESET} "
        f"{_DIM}(numeric: between/within variance F; categorical: Cramér's V — "
        f"higher = cleaner separation){_RESET}"
    )
    for name, score, groups in rows:
        if name in byte_names:
            mark = f"{_DIM}·{_RESET}"
        else:
            mark = _mark_for(name, parameters, candidate_names)
        disp = relabel_signal(name, notation, sub_bytes=sub_bytes)
        try_tag = f" {_CYAN}(try){_RESET}" if name in candidate_names else ""

        # Categorical params: report Cramér's V vs state (nominal association)
        # instead of the interval-scale F, which doesn't apply to a mode/flag set.
        if name in cat_pairs:
            from canlib.stats import cramers_v

            xs, ys = cat_pairs[name]
            v = cramers_v(xs, ys)
            if v is None:
                print(f"    {mark} {disp}{try_tag}  {_DIM}V=n/a{_RESET}")
                continue
            vcolor = _GREEN if v >= 0.6 else _YELLOW if v >= 0.3 else _DIM
            distinct = "  ".join(sorted({str(c) for c in xs})[:6])
            print(
                f"    {mark} {disp}{try_tag}  {vcolor}V={v:.2f}{_RESET}  "
                f"{_DIM}(categorical: {distinct}){_RESET}"
            )
            continue

        if score is None:
            print(f"    {mark} {disp}{try_tag}  {_DIM}F=n/a{_RESET}")
            continue
        color = _GREEN if score >= 10 or score == float("inf") else _YELLOW if score >= 2 else _DIM
        fstr = "∞" if score == float("inf") else f"{score:.1f}"
        means = "  ".join(f"{g}={sum(v) / len(v):.1f}" for g, v in groups.items() if v)
        print(f"    {mark} {disp}{try_tag}  {color}F={fstr}{_RESET}  {_DIM}{means}{_RESET}")
    print()


def print_mirrors(
    all_results: list[dict],
    *,
    bits: bool = False,
    notation: ByteNotation = ByteNotation.WICAN,
    sub_bytes: int = 1,
) -> None:
    """Print exact byte/bit mirrors (redundant signals) found across captures."""
    mirrors = find_mirrors(all_results, bits=bits)
    print(f"  {_BOLD}Exact mirrors{_RESET} {_DIM}(positions equal across all captures){_RESET}")
    if not mirrors:
        print(f"    {_DIM}none{_RESET}")
        print()
        return
    for a, b, n in mirrors:
        da = relabel_signal(a, notation, sub_bytes=sub_bytes)
        db = relabel_signal(b, notation, sub_bytes=sub_bytes)
        print(f"    {_GREEN}{da} == {db}{_RESET}  {_DIM}(n={n}){_RESET}")
    print()


def print_correlations(
    all_results: list[dict],
    param_names: list[str],
    parameters: dict,
    candidate_names: set[str],
    ref: str,
    *,
    cross_ref_series: list[TimePoint] | None = None,
    cross_ref_label: str | None = None,
    tol_s: float | None = None,
    transform: str | None = None,
    method: str = "pearson",
) -> None:
    """Correlation of every parameter against ``ref`` across captures.

    The key reverse-engineering lever: correlate a candidate expression against
    a known signal (e.g. a torque guess vs MCU_MOTOR_RPM) to confirm it tracks.

    When ``cross_ref_series`` (a list[TimePoint] from another ECU/PID) is given,
    every local param is time-aligned against it by nearest timestamp instead of
    the fast same-payload positional pairing. ``transform`` optionally reshapes
    the reference series first (e.g. ``delta`` to test level vs rate); ``method``
    selects pearson (linear) or spearman (monotone/rank).
    """
    from canlib.align import DEFAULT_JOIN_TOL_S, join_nearest

    cross = cross_ref_series is not None
    ref_label = cross_ref_label if cross else ref
    tol = tol_s if tol_s is not None else DEFAULT_JOIN_TOL_S

    if cross and transform and transform != "raw":
        cross_ref_series = _transform_series(cross_ref_series, transform)

    rows = []
    for name in param_names:
        if not cross and name == ref:
            continue
        if cross:
            local = _local_series(all_results, name)
            # cross implies cross_ref_series is not None (set together above)
            assert cross_ref_series is not None
            # join_nearest(ref, cand): keep ref as the external signal
            xs, ys, n = join_nearest(cross_ref_series, local, tol_s=tol)
            r = _correlation(xs, ys, method)
            rows.append((name, r, n))
        else:
            if transform and transform != "raw":
                # delta/cumsum are order-sensitive: pair in time order, not
                # capture-list order, so the reference transform is meaningful.
                xs, ys = _paired_timed(all_results, ref, name)
                xs = apply_transform(xs, transform)
            else:
                xs, ys = _paired(all_results, ref, name)
            r = _correlation(xs, ys, method)
            rows.append((name, r, len(xs)))
    # Strongest absolute correlations first; undefined (None) last.
    rows.sort(key=lambda t: (t[1] is None, -abs(t[1]) if t[1] is not None else 0))

    coeff = {
        "spearman": "Spearman ρ",
        "cramers_v": "Cramér's V",
        "mutual_info": "norm. MI",
    }.get(method, "Pearson r")
    header = f"  {_BOLD}Correlation vs {ref_label}{_RESET} {_DIM}({coeff}"
    if transform and transform != "raw":
        header += f", ref {transform}"
    if cross:
        header += f", nearest-join ≤{tol:g}s"
    header += f"){_RESET}"
    print(header)
    for name, r, n in rows:
        mark = _mark_for(name, parameters, candidate_names)
        try_tag = f" {_CYAN}(try){_RESET}" if name in candidate_names else ""
        if r is None:
            print(f"    {mark} {name}{try_tag}  {_DIM}r=n/a  n={n}{_RESET}")
            continue
        color = _GREEN if abs(r) >= 0.7 else _YELLOW if abs(r) >= 0.3 else _DIM
        print(f"    {mark} {name}{try_tag}  {color}r={r:+.3f}{_RESET}  {_DIM}n={n}{_RESET}")
    print()


def _entry_dt(cap: dict):
    """Real datetime for a capture (session date + per-capture time), or None."""
    from canlib.capture_dates import entry_datetime

    return entry_datetime(cap)


def _dump_column_label(off: int, include_pci: bool, notation: ByteNotation, sub_bytes: int) -> str:
    """Column label for WiCAN offset ``off`` in the byte-matrix export."""
    from canlib.byteindex import wican_to_isotp
    from canlib.notation import ByteRef

    if wican_to_isotp(off) is None:
        return f"B{off}"  # PCI framing byte — no ISO-TP position, WiCAN label only
    try:
        return ByteRef.from_wican(off).render(notation, sub_bytes=sub_bytes)
    except ValueError:
        return f"B{off}"


def _dump_bytes(
    all_results: list[dict],
    ecu_key: str,
    pid_key: str,
    *,
    as_json: bool,
    include_pci: bool,
    notation: ByteNotation,
    sub_bytes: int,
) -> int:
    """Emit a ``timestamp × byte-offset`` matrix, one row per capture.

    The first-class, structured replacement for regex-scraping ``captures --diff``
    text: dump the raw byte values of every scoped capture as CSV (default) or
    JSON for ad-hoc analysis. Columns are WiCAN ``Bnn`` (relabelled by
    ``--notation``); ISO-TP PCI framing bytes are skipped unless ``include_pci``.
    A capture shorter than the widest frame leaves trailing cells blank/null.
    """
    from canlib.byteindex import payload_to_wican_bytes, wican_to_isotp

    rows: list[tuple[dict, bytes]] = []
    max_len = 0
    for r in all_results:
        cap = r["capture"]
        try:
            fr = payload_to_wican_bytes(cap["payload"])
        except Exception:
            fr = b""
        rows.append((cap, fr))
        max_len = max(max_len, len(fr))

    offsets = [off for off in range(max_len) if include_pci or wican_to_isotp(off) is not None]
    labels = [_dump_column_label(off, include_pci, notation, sub_bytes) for off in offsets]

    def _row_time(cap: dict) -> str:
        dt = _entry_dt(cap)
        if dt is not None:
            return dt.isoformat()
        return f"{cap.get('date', '')} {cap.get('time', '')}".strip()

    if as_json:
        out = {
            "ecu": ecu_key,
            "pid": pid_key,
            "notation": notation.value,
            "include_pci": include_pci,
            "columns": labels,
            "offsets": offsets,
            "rows": [
                {
                    "time": _row_time(cap),
                    "date": str(cap.get("date", "")),
                    "vehicle_states": cap.get("vehicle_states") or [],
                    "bytes": {
                        lbl: (fr[off] if off < len(fr) else None)
                        for lbl, off in zip(labels, offsets, strict=True)
                    },
                }
                for cap, fr in rows
            ],
        }
        json.dump(out, sys.stdout, indent=2, default=str)
        print()
        return 0

    import csv

    writer = csv.writer(sys.stdout)
    writer.writerow(["time", "ecu", "pid", *labels])
    for cap, fr in rows:
        writer.writerow(
            [
                _row_time(cap),
                ecu_key,
                pid_key,
                *[(fr[off] if off < len(fr) else "") for off in offsets],
            ]
        )
    return 0
