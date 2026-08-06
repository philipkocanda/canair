"""The signal-value views: value ranges, the compact log, and statistics.

What ``canair decode`` prints when you are reading *values* — the default
per-signal range table, ``--compact``'s one-row-per-capture log, and
``--stats``/``--group-by``. The analysis rankings live in :mod:`.analysis` and the
raw byte matrix in :mod:`.dump`.
"""

from __future__ import annotations

from canlib.states import join_states as _join_states
from canlib.stats import compute_stats
from canlib.stats import fmt_num as _fmt_num

from .calc import _series
from .format import (
    _BOLD,
    _CYAN,
    _DIM,
    _GREEN,
    _RED,
    _RESET,
    _YELLOW,
    _compact_cell,
    _mark_for,
    check_range,
    format_value,
)


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
