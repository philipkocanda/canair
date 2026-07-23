#!/usr/bin/env python3
"""Decode captured UDS payloads using PID parameter definitions.

Takes an ECU+PID, loads all matching captures, applies WiCAN expressions
from the YAML PID definitions, and reports how each decoded *parameter value*
behaves across the full capture history. Parameter/value-centric and focused
on validating expressions — for payload/byte-level views (hex, byte-diff,
dedup, cross-ECU, dates) use `canair captures` instead.

By default it prints each parameter's value range (min-max, or constant) across
all captures. Use --compact for a chronological one-line-per-capture view, or
--try to test a candidate expression without editing YAML.

Scope which captures are considered with --since/--until/--date (like `canair
captures`) and --state/--label (case-insensitive substring of the session state
or label — the natural unit of drive analysis). --first/--last N slice the
matching captures chronologically.

Examples:
  canair decode BMS 2101              # Value range of every param across captures
  canair decode BMS 2101 --param SOC_BMS SOC_DISP  # Only specific params
  canair decode IGPM 22BC03           # Decode IGPM DID BC03
  canair decode BMS 2101 --verified   # Only verified parameters
  canair decode BMS 2101 --unverified # Only unverified parameters (validation focus)
  canair decode BMS 2101 --compact    # One line per capture (value evolution)
  canair decode ESC 22C101 --state 'MT->KW' --compact --changes-only  # One drive, stationary runs collapsed
  canair decode MCU 2102 --stats --group-by state  # Per-drive-segment statistics
  canair decode VCU 2101 --date 2026-07-22 --last 20  # Last 20 captures of one day
  canair decode BMS 2101 --json       # JSON (per-capture decoded values)
  canair decode MCU 2102 --stats      # Descriptive stats per param (mean/median/stdev/distinct)
  canair decode MCU 2102 --corr MCU_MOTOR_RPM   # Correlate every param vs a known signal
  canair decode MCU 2102 --plot                      # sweep interpretations, find the signal
  canair decode MCU 2102 --plot --corr MCU_MOTOR_RPM # overlay a known signal + live r
  canair decode MCU 2102 --try "TORQUE:Nm=[S12:S13]/100"   # Test a candidate expression
  canair decode MCU 2102 --try "T=[S17:S18]" --corr MCU_MOTOR_RPM  # Validate a candidate by correlation
  canair decode MCU 21F2 --try "X=B9" --try "Y=[S10:S11]"  # Multiple candidates, undefined PID OK
"""

import argparse
import json
import math
import sys

from canlib.capture_dates import (
    add_scope_args,
    filter_by_date_range,
    filter_by_text,
    resolve_date_bounds,
)
from canlib.commands._decode_plot import (
    POST_TRANSFORMS,
    apply_transform,
    cmd_plot,
)
from canlib.commands._hints import ecu_completer as _ecu_completer
from canlib.commands._hints import pid_completer as _pid_completer
from canlib.expression import evaluate_expression
from canlib.pids import build_ecu_index, load_pids
from canlib.states import join_states as _join_states

NAME = "decode"

# ANSI colors
_BOLD = "\033[1m"
_DIM = "\033[2m"
_GREEN = "\033[92m"
_YELLOW = "\033[93m"
_RED = "\033[91m"
_CYAN = "\033[96m"
_RESET = "\033[0m"


def load_captures(ecu: str, pid: str) -> list[dict]:
    """Load all payload captures matching ECU+PID from capture files.

    Thin wrapper over the canonical :func:`commands.captures.load_all_captures`
    (one loader for the whole tool), narrowed to a single ECU+PID and reshaped to
    the slim dict decode's views expect: ``{file, date, label, vehicle_states,
    payload, notes, time}``. Capture ``ecu`` addresses are resolved to canonical
    short names by the shared loader.
    """
    from canlib.commands.captures import load_all_captures

    entries = []
    for e in load_all_captures():
        if str(e.get("ecu", "")).upper() != ecu.upper():
            continue
        if str(e.get("pid", "")).upper() != pid.upper():
            continue
        if not e.get("payload"):
            continue
        entries.append(
            {
                "file": e.get("file", ""),
                "date": str(e.get("date", "")),
                "label": e.get("session_label", ""),
                "vehicle_states": list(e.get("vehicle_states") or []),
                "payload": e["payload"],
                "notes": e.get("notes", ""),
                "time": e.get("time", ""),
            }
        )
    return entries


def scope_captures(
    entries: list[dict],
    *,
    since=None,
    until=None,
    state=None,
    label=None,
    first=None,
    last=None,
) -> list[dict]:
    """Apply date/state/label range and first/last slicing to loaded captures.

    Date/text filters run first (they define *what* matches); ``first``/``last``
    then slice the chronologically-ordered survivors. ``first`` and ``last`` are
    applied in that order, so combining them yields the first ``first`` then its
    last ``last`` (rarely useful, but well-defined). Entries are assumed already
    in capture (chronological) order from :func:`load_captures`.
    """
    entries = filter_by_date_range(entries, since, until)
    entries = filter_by_text(entries, state=state, label=label)
    if first is not None and first >= 0:
        entries = entries[:first]
    if last is not None and last >= 0:
        entries = entries[-last:] if last else []
    return entries


def payload_to_wican_bytes(payload_hex: str) -> bytes:
    """Convert raw UDS payload hex to WiCAN frame bytes (with PCI inserted).

    Delegates to the canonical converter in ``byteindex`` (one PCI-reconstruction
    path for the whole tool); kept as a re-export for decode's callers/tests.
    """
    from canlib.byteindex import payload_to_wican_bytes as _to_bytes

    return _to_bytes(payload_hex)


def decode_payload(wican_bytes: bytes, parameters: dict) -> dict[str, dict]:
    """Evaluate all parameter expressions against a WiCAN frame.

    Returns dict: param_name -> {value, expression, unit, verified, error}.
    """
    results = {}
    for name, param in parameters.items():
        expr = param.get("expression", "")
        if not expr:
            continue
        try:
            value = evaluate_expression(expr, wican_bytes)
            results[name] = {
                "value": value,
                "expression": expr,
                "unit": param.get("unit", ""),
                "verified": param.get("verified", False),
                "min": param.get("min"),
                "max": param.get("max"),
            }
        except Exception as e:
            results[name] = {
                "value": None,
                "expression": expr,
                "unit": param.get("unit", ""),
                "verified": param.get("verified", False),
                "error": str(e),
            }
    return results


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


def _scope_banner(since, until, state, label, first, last) -> str:
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


def parse_try_expr(arg: str) -> tuple[str, str, str]:
    """Parse a ``--try`` argument ``NAME[:unit]=EXPR`` into (name, unit, expr).

    The split is on the first ``=`` so expressions may contain ``:`` (e.g.
    ``[S10:S11]``); an optional unit is taken from ``NAME:unit`` on the left.
    """
    left, sep, expr = arg.partition("=")
    if not sep or not left.strip() or not expr.strip():
        raise ValueError(f"invalid --try {arg!r} (expected NAME[:unit]=EXPR)")
    name, _, unit = left.partition(":")
    if not name.strip():
        raise ValueError(f"invalid --try {arg!r} (empty parameter name)")
    return name.strip(), unit.strip(), expr.strip()


def build_try_params(try_args: list[str]) -> dict:
    """Build synthetic (unverified, candidate) parameter defs from ``--try`` args."""
    params: dict[str, dict] = {}
    for arg in try_args:
        name, unit, expr = parse_try_expr(arg)
        params[name] = {"expression": expr, "unit": unit, "verified": False, "candidate": True}
    return params


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


def _mean(xs: list[float]) -> float:
    return sum(xs) / len(xs)


def _median(xs: list[float]) -> float:
    s = sorted(xs)
    n = len(s)
    return s[n // 2] if n % 2 else (s[n // 2 - 1] + s[n // 2]) / 2


def _stdev(xs: list[float]) -> float:
    """Sample standard deviation (0.0 for fewer than two points)."""
    if len(xs) < 2:
        return 0.0
    m = _mean(xs)
    return (sum((x - m) ** 2 for x in xs) / (len(xs) - 1)) ** 0.5


def _pearson(xs: list[float], ys: list[float]) -> float | None:
    """Pearson correlation of paired series, or None if undefined."""
    n = len(xs)
    if n < 2:
        return None
    mx, my = _mean(xs), _mean(ys)
    sx = sum((x - mx) ** 2 for x in xs)
    sy = sum((y - my) ** 2 for y in ys)
    if sx == 0 or sy == 0:
        return None
    cov = sum((x - mx) * (y - my) for x, y in zip(xs, ys, strict=True))
    return cov / (sx**0.5 * sy**0.5)


def _series(all_results: list[dict], name: str) -> list[float]:
    """Decoded values for one param across captures (capture order, None dropped)."""
    return [
        r["decoded"][name]["value"]
        for r in all_results
        if name in r["decoded"] and r["decoded"][name].get("value") is not None
    ]


def _paired(all_results: list[dict], ref: str, name: str) -> tuple[list[float], list[float]]:
    """Time-aligned (ref, name) value pairs across captures where both are present."""
    xs: list[float] = []
    ys: list[float] = []
    for r in all_results:
        d = r["decoded"]
        rv = d.get(ref, {}).get("value")
        pv = d.get(name, {}).get("value")
        if rv is not None and pv is not None:
            xs.append(rv)
            ys.append(pv)
    return xs, ys


def _paired_timed(all_results: list[dict], ref: str, name: str) -> tuple[list[float], list[float]]:
    """Like :func:`_paired` but ordered by capture timestamp.

    Needed for order-sensitive reference transforms (``delta``/``cumsum``): the
    capture-list order is not guaranteed to be chronological (recovered journals,
    edits), so a positional pairing would corrupt a rate. Undated captures sort
    last (``datetime.max``) so they never split a dated run.
    """
    from datetime import datetime

    from canlib.capture_dates import entry_datetime

    triples: list[tuple[datetime, float, float]] = []
    for r in all_results:
        d = r["decoded"]
        rv = d.get(ref, {}).get("value")
        pv = d.get(name, {}).get("value")
        if rv is None or pv is None:
            continue
        triples.append((entry_datetime(r.get("capture") or {}) or datetime.max, rv, pv))
    triples.sort(key=lambda t: t[0])
    return [t[1] for t in triples], [t[2] for t in triples]


def _local_series(all_results: list[dict], name: str) -> list:
    """A time-stamped series (list[TimePoint]) for one local param.

    Used to time-align local params against a cross-signal reference on a
    different ECU/PID. Captures with no usable ``datetime`` are dropped.
    """
    from canlib.align import TimePoint
    from canlib.capture_dates import entry_datetime

    out = []
    for r in all_results:
        v = r["decoded"].get(name, {}).get("value")
        if v is None:
            continue
        dt = entry_datetime(r["capture"])
        if dt is None:
            continue
        out.append(TimePoint(dt, float(v)))
    return out


def load_cross_ref_series(ref: str, *, scope: dict, tol_s: float):
    """Load an external ``ECU:PID:PARAM|EXPR`` reference as a TimePoint series.

    Returns ``(series, resolved_label)`` or raises ``ValueError`` with a clean
    message. Applies the same date/state/label scope as the local decode so the
    reference is drawn from the same drive/window.
    """
    from canlib.align import SignalRef, extract_series, load_signal_captures
    from canlib.pids import build_ecu_index, load_pids

    sref = SignalRef.parse(ref)
    loaded = load_signal_captures(
        [(sref.ecu, sref.pid)],
        since=scope.get("since"),
        until=scope.get("until"),
        state=scope.get("state"),
        label=scope.get("label"),
    )
    lp = loaded[(sref.ecu.upper(), sref.pid.upper())]
    if not lp.captures:
        raise ValueError(
            f"no timed captures for reference {sref.ecu}:{sref.pid} in scope"
            + (f" ({lp.n_no_time} untimed skipped)" if lp.n_no_time else "")
        )
    # Resolve a defined param name to its expression when possible.
    params: dict = {}
    ecu_index = build_ecu_index(load_pids())
    ecu_pids = ecu_index.get(sref.ecu.upper(), {}).get("pids", {})
    if sref.pid.upper() in ecu_pids:
        params = ecu_pids[sref.pid.upper()]["parameters"]
    series = extract_series(lp, sref.name_or_expr, parameters=params)
    if not series:
        raise ValueError(f"reference {sref.label} decoded no numeric values in scope")
    return series, sref.label


def _fmt_num(x: float) -> str:
    """Compact numeric formatting: integers stay integral, else 2 decimals.

    Non-finite values (which float byte-interpretations routinely produce) are
    rendered as text rather than crashing ``int()``.
    """
    if not math.isfinite(x):
        return "nan" if math.isnan(x) else ("inf" if x > 0 else "-inf")
    if x == int(x):
        return str(int(x))
    return f"{x:.2f}"


def compute_stats(values: list[float]) -> dict:
    """Descriptive statistics for one parameter's value series."""
    distinct = sorted(set(values))
    return {
        "n": len(values),
        "distinct": len(distinct),
        "min": min(values),
        "max": max(values),
        "mean": _mean(values),
        "median": _median(values),
        "stdev": _stdev(values),
        "values": distinct,
    }


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


def _discriminability(groups: dict[str, list[float]]) -> float | None:
    """F-like score: between-group variance / within-group (pooled) variance.

    High when a signal is nearly constant within each state but differs across
    states (a mode/thermal/relay signal). ``None`` when undefined (too few
    groups/points).
    """
    pops = [vals for vals in groups.values() if len(vals) >= 2]
    if len(pops) < 2:
        return None
    all_vals = [v for vals in pops for v in vals]
    n = len(all_vals)
    grand = sum(all_vals) / n
    between = sum(len(vals) * (sum(vals) / len(vals) - grand) ** 2 for vals in pops)
    within = sum((v - sum(vals) / len(vals)) ** 2 for vals in pops for v in vals)
    df_between = len(pops) - 1
    df_within = n - len(pops)
    if df_between <= 0 or df_within <= 0:
        return None
    msb = between / df_between
    msw = within / df_within
    if msw == 0:
        # Perfect separation with zero within-group spread: rank very high but
        # finite ordering falls back to between-group spread.
        return float("inf") if msb > 0 else None
    return msb / msw


def _byte_state_buckets(
    all_results: list[dict], field: str, *, min_distinct: int = 2
) -> dict[str, dict[str, list[float]]]:
    """Bucket each varying, non-PCI raw byte value by session ``field``.

    The raw-byte analogue of the param buckets in :func:`print_discriminate`.
    Uses a low ``min_distinct`` (2) on purpose: the highest-value discrimination
    targets are near-binary relay/mode bytes (e.g. 0x00/0x34) that the default
    correlation floor (4) would drop. Reads every capture (incl. untimed —
    discrimination buckets by state, not time) and skips PCI framing bytes via
    the canonical :func:`byteindex.wican_to_isotp` detector.
    """
    from canlib.byteindex import payload_to_wican_bytes, wican_to_isotp

    frames: list[tuple[bytes, str]] = []
    max_len = 0
    for r in all_results:
        cap = r["capture"]
        try:
            fr = payload_to_wican_bytes(cap["payload"])
        except Exception:
            continue
        state = _join_states(cap.get("vehicle_states")) or "(no state)"
        frames.append((fr, state))
        max_len = max(max_len, len(fr))

    buckets: dict[str, dict[str, list[float]]] = {}
    for off in range(max_len):
        if wican_to_isotp(off) is None:
            continue  # PCI framing byte, not data
        per_state: dict[str, list[float]] = {}
        distinct: set[float] = set()
        for fr, state in frames:
            if off < len(fr):
                v = float(fr[off])
                per_state.setdefault(state, []).append(v)
                distinct.add(v)
        if len(distinct) >= min_distinct:
            buckets[f"B{off}"] = per_state
    return buckets


def print_discriminate(
    all_results: list[dict],
    param_names: list[str],
    parameters: dict,
    candidate_names: set[str],
    field: str,
    *,
    include_bytes: bool = False,
) -> None:
    """Rank params (and optionally raw bytes) by how cleanly they separate across
    session ``field`` groups.

    The confirmation lever for state-dependent signals (thermal/mode/relay) that
    a driving-anchor correlation misses — e.g. MCU inverter temp reads distinctly
    across charging/ready/driving. Uses an F-like between/within variance ratio.

    With ``include_bytes`` (``--bytes``), every varying non-PCI raw byte is ranked
    alongside the params — finding a state-dependent byte without first defining a
    ``--try`` candidate for it.
    """
    buckets: dict[str, dict[str, list[float]]] = {name: {} for name in param_names}
    for r in all_results:
        key = _join_states(r["capture"].get("vehicle_states")) or "(no state)"
        for name in param_names:
            v = r["decoded"].get(name, {}).get("value")
            if v is not None:
                buckets[name].setdefault(key, []).append(v)

    byte_names: set[str] = set()
    if include_bytes:
        byte_buckets = _byte_state_buckets(all_results, field)
        byte_names = set(byte_buckets)
        buckets.update(byte_buckets)

    rows = []
    for name in list(buckets):
        score = _discriminability(buckets[name])
        rows.append((name, score, buckets[name]))
    rows.sort(key=lambda t: (t[1] is None, -(t[1] if t[1] is not None else 0)))

    hdr_extra = " (params + bytes)" if include_bytes else ""
    print(
        f"  {_BOLD}Discriminability by {field}{hdr_extra}{_RESET} "
        f"{_DIM}(between/within variance F; higher = cleaner separation){_RESET}"
    )
    for name, score, groups in rows:
        if name in byte_names:
            mark = f"{_DIM}·{_RESET}"
        else:
            mark = _mark_for(name, parameters, candidate_names)
        try_tag = f" {_CYAN}(try){_RESET}" if name in candidate_names else ""
        if score is None:
            print(f"    {mark} {name}{try_tag}  {_DIM}F=n/a{_RESET}")
            continue
        color = _GREEN if score >= 10 or score == float("inf") else _YELLOW if score >= 2 else _DIM
        fstr = "∞" if score == float("inf") else f"{score:.1f}"
        means = "  ".join(f"{g}={sum(v) / len(v):.1f}" for g, v in groups.items() if v)
        print(f"    {mark} {name}{try_tag}  {color}F={fstr}{_RESET}  {_DIM}{means}{_RESET}")
    print()


def find_mirrors(all_results: list[dict], *, bits: bool = False) -> list[tuple[str, str, int]]:
    """Find byte (and optionally bit) positions that are exactly equal across
    every capture — redundant status mirrors and unit-variants.

    Returns ``(a, b, n)`` tuples where signal ``a`` == signal ``b`` in all ``n``
    captures. Byte positions are ``Bn``; bits are ``Bn:k``. Only positions that
    actually vary (≥2 distinct values) are considered, so all-constant padding
    doesn't produce spurious "mirrors".
    """
    frames: list[bytes] = []
    for r in all_results:
        cap = r["capture"]
        try:
            frames.append(payload_to_wican_bytes(cap["payload"]))
        except Exception:
            continue
    if len(frames) < 2:
        return []
    max_len = min(len(f) for f in frames)  # only positions present in every frame

    # Collect per-position value columns for varying byte positions.
    byte_cols: dict[str, list[int]] = {}
    for i in range(max_len):
        col = [f[i] for f in frames]
        if len(set(col)) >= 2:
            byte_cols[f"B{i}"] = col
    if bits:
        for i in range(max_len):
            for k in range(8):
                col = [(f[i] >> k) & 1 for f in frames]
                if len(set(col)) >= 2:
                    byte_cols[f"B{i}:{k}"] = col

    names = list(byte_cols)
    mirrors: list[tuple[str, str, int]] = []
    for i in range(len(names)):
        for j in range(i + 1, len(names)):
            a, b = names[i], names[j]
            # A byte and one of its own bits trivially "mirror" for single-bit
            # bytes; skip a bit compared against its own containing byte.
            if a.split(":")[0] == b.split(":")[0] and (":" in a) != (":" in b):
                continue
            if byte_cols[a] == byte_cols[b]:
                mirrors.append((a, b, len(frames)))
    return mirrors


def print_mirrors(all_results: list[dict], *, bits: bool = False) -> None:
    """Print exact byte/bit mirrors (redundant signals) found across captures."""
    mirrors = find_mirrors(all_results, bits=bits)
    print(f"  {_BOLD}Exact mirrors{_RESET} {_DIM}(positions equal across all captures){_RESET}")
    if not mirrors:
        print(f"    {_DIM}none{_RESET}")
        print()
        return
    for a, b, n in mirrors:
        print(f"    {_GREEN}{a} == {b}{_RESET}  {_DIM}(n={n}){_RESET}")
    print()


def resolve_ref(ref: str, param_names: list[str]) -> str | None:
    """Case-insensitively resolve a --corr reference to an actual param name."""
    for n in param_names:
        if n.upper() == ref.upper():
            return n
    return None


def print_correlations(
    all_results: list[dict],
    param_names: list[str],
    parameters: dict,
    candidate_names: set[str],
    ref: str,
    *,
    cross_ref_series=None,
    cross_ref_label: str | None = None,
    tol_s: float | None = None,
    transform: str | None = None,
) -> None:
    """Pearson correlation of every parameter against ``ref`` across captures.

    The key reverse-engineering lever: correlate a candidate expression against
    a known signal (e.g. a torque guess vs MCU_MOTOR_RPM) to confirm it tracks.

    When ``cross_ref_series`` (a list[TimePoint] from another ECU/PID) is given,
    every local param is time-aligned against it by nearest timestamp instead of
    the fast same-payload positional pairing. ``transform`` optionally reshapes
    the reference series first (e.g. ``delta`` to test level vs rate).
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
            # join_nearest(ref, cand): keep ref as the external signal
            xs, ys, n = join_nearest(cross_ref_series, local, tol_s=tol)
            r = _pearson(xs, ys)
            rows.append((name, r, n))
        else:
            if transform and transform != "raw":
                # delta/cumsum are order-sensitive: pair in time order, not
                # capture-list order, so the reference transform is meaningful.
                xs, ys = _paired_timed(all_results, ref, name)
                xs = apply_transform(xs, transform)
            else:
                xs, ys = _paired(all_results, ref, name)
            r = _pearson(xs, ys)
            rows.append((name, r, len(xs)))
    # Strongest absolute correlations first; undefined (None) last.
    rows.sort(key=lambda t: (t[1] is None, -abs(t[1]) if t[1] is not None else 0))

    header = f"  {_BOLD}Correlation vs {ref_label}{_RESET} {_DIM}(Pearson r"
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


def _transform_series(series, mode: str):
    """Apply a POST_TRANSFORMS mode to a TimePoint series (preserving times)."""
    from canlib.align import TimePoint

    vals = apply_transform([tp.value for tp in series], mode)
    return [TimePoint(tp.dt, v) for tp, v in zip(series, vals, strict=True)]




# ---------------------------------------------------------------------------
# Plot mode (interactive signal exploration)
# ---------------------------------------------------------------------------
#
# The interactive explorer + its interpretation/transform primitives live in
# _decode_plot.py (leaf module) and are imported at the top of this file.


def add_parser(subparsers) -> argparse.ArgumentParser:
    parser = subparsers.add_parser(
        NAME,
        help="Decode captured UDS payloads using PID parameter definitions",
        description="Decode captured UDS payloads using PID parameter definitions.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__.split("Examples:")[1] if "Examples:" in __doc__ else "",
    )
    parser.add_argument(
        "ecu", nargs="?", help="ECU name (e.g., BMS, IGPM, BCM)"
    ).completer = _ecu_completer
    parser.add_argument(
        "pid", nargs="?", help="PID code (e.g., 2101, 22BC03)"
    ).completer = _pid_completer
    parser.add_argument("--param", nargs="+", metavar="NAME", help="Show only specific parameters")
    parser.add_argument("--verified", action="store_true", help="Show only verified parameters")
    parser.add_argument("--unverified", action="store_true", help="Show only unverified parameters")
    parser.add_argument(
        "--json", action="store_true", help="Output as JSON (per-capture decoded values)"
    )
    parser.add_argument(
        "--compact",
        action="store_true",
        help="One line per capture (chronological param=value pairs)",
    )
    parser.add_argument(
        "--changes-only",
        "-c",
        action="store_true",
        help="With --compact: skip rows where all shown params are "
        "unchanged from the previous row (collapses stationary runs)",
    )
    parser.add_argument(
        "--stats",
        action="store_true",
        help="Descriptive statistics per param (n, distinct, mean, median, stdev)",
    )
    parser.add_argument(
        "--group-by",
        choices=["state", "vehicle_states"],
        metavar="FIELD",
        help="With --stats: compute statistics per session FIELD "
        "(currently 'state') instead of pooling all captures",
    )
    parser.add_argument(
        "--discriminate",
        choices=["state"],
        metavar="FIELD",
        help="Rank params/bytes by how cleanly they separate across session "
        "FIELD groups (F = between/within variance) — finds state-dependent "
        "signals (thermal/mode/relay) a driving correlation misses",
    )
    parser.add_argument(
        "--find-mirrors",
        action="store_true",
        help="Report byte positions that are exactly equal across all captures "
        "(redundant status mirrors / unit-variants); add --bits for bit-level",
    )
    parser.add_argument(
        "--bits",
        action="store_true",
        help="With --find-mirrors: also compare individual bits (Bn:k)",
    )
    parser.add_argument(
        "--bytes",
        action="store_true",
        help="With --discriminate: also rank every varying raw byte (Bn), not "
        "just defined params — finds state-dependent bytes without a --try",
    )
    parser.add_argument(
        "--first", type=int, metavar="N", help="Only the first N matching captures (chronological)"
    )
    parser.add_argument(
        "--last", type=int, metavar="N", help="Only the last N matching captures (chronological)"
    )
    parser.add_argument(
        "--corr",
        metavar="PARAM",
        help="Correlate every param (incl. --try) against PARAM (Pearson r). "
        "PARAM may be a local param name, or a cross-signal reference "
        "ECU:PID:PARAM or ECU:PID:EXPR (e.g. ESC:22C101:REAL_SPEED_KMH) which is "
        "time-aligned by nearest timestamp.",
    )
    parser.add_argument(
        "--join-tol",
        type=float,
        default=None,
        metavar="SECONDS",
        help="Nearest-timestamp join window for a cross-signal --corr "
        "(default 2.5s)",
    )
    parser.add_argument(
        "--corr-transform",
        choices=list(POST_TRANSFORMS),
        metavar="MODE",
        help="Transform the --corr reference before pairing "
        "(raw/delta/abs/cumsum/normalize/smooth) — e.g. --corr-transform delta "
        "to test whether a signal tracks a reference's RATE rather than its level",
    )
    parser.add_argument(
        "--plot",
        action="store_true",
        help="Interactive signal explorer: sweep byte interpretations "
        "(u8/i16/f32/... and endianness) and params, plot across captures, "
        "apply transforms (delta/abs/normalize/...), zoom/pan the x-axis, "
        "overlay a --corr signal, and flag bytes already mapped by a param",
    )
    parser.add_argument(
        "--try",
        dest="try_expr",
        action="append",
        metavar="NAME[:unit]=EXPR",
        help="Evaluate a candidate expression against captures without editing "
        "YAML (repeatable; works even if the PID has no params defined yet)",
    )
    add_scope_args(parser)
    parser.set_defaults(func=run)
    return parser


def run(args) -> int:
    # Friendly guidance when the ECU/PID selectors are missing.
    if not args.ecu:
        from canlib.commands._hints import ecu_hint

        print("Specify an ECU and PID to decode, e.g. `canair decode BMS 2101`.\n")
        print(ecu_hint())
        return 2
    # Accept an ECU-registry alias (e.g. LDC for OBC, ABS for ESC) or any case,
    # matching `canair captures`. Canonicalises to the ecus/ key before lookup.
    from canlib.ecus import canonical_ecu_name_safe

    args.ecu = canonical_ecu_name_safe(args.ecu)
    if not args.pid:
        from canlib.commands._hints import pid_hint

        print(f"Specify a PID for {args.ecu.upper()}, e.g. `canair decode {args.ecu} 2101`.\n")
        print(pid_hint(args.ecu))
        return 2

    # --plot and --try tolerate a not-yet-defined ECU/PID (raw byte inspection).
    tolerate_missing = bool(args.try_expr) or args.plot or args.find_mirrors

    # Resolve date scoping (--date shorthand for equal since/until; validated here).
    since, until, err = resolve_date_bounds(args)
    if err:
        print(f"error: {err}", file=sys.stderr)
        return 2

    # Modifier flags depend on their base view; fail loud rather than silently no-op.
    if args.changes_only and not args.compact:
        print("error: --changes-only requires --compact", file=sys.stderr)
        return 2
    if args.group_by and not args.stats:
        print("error: --group-by requires --stats", file=sys.stderr)
        return 2
    if args.corr_transform and not args.corr:
        print("error: --corr-transform requires --corr", file=sys.stderr)
        return 2

    # Build any candidate expressions from --try (validated early for a clean error).
    try:
        try_params = build_try_params(args.try_expr) if args.try_expr else {}
    except ValueError as e:
        print(f"Error: {e}")
        return 1
    candidate_names = set(try_params)

    # Load PID definitions
    pids_data = load_pids()
    ecu_index = build_ecu_index(pids_data)

    ecu_key = args.ecu.upper()
    pid_key = args.pid.upper()

    # Resolve defined parameters. With --try we tolerate an unknown ECU/PID so a
    # brand-new PID (captured but not yet defined) can still be probed.
    parameters: dict = {}
    if ecu_key in ecu_index:
        ecu_pids = ecu_index[ecu_key]["pids"]
        if pid_key in ecu_pids:
            parameters = ecu_pids[pid_key]["parameters"]
        elif not tolerate_missing:
            print(
                f"PID '{args.pid}' not found for {ecu_key}. Available: {', '.join(sorted(ecu_pids))}"
            )
            return 1
    elif not tolerate_missing:
        print(f"ECU '{args.ecu}' not found in ecus/. Available: {', '.join(sorted(ecu_index))}")
        return 1

    # Full (unfiltered) defined params for this PID — used by --plot to flag
    # bytes that are already mapped, independent of --param/--verified filters.
    defined_params = dict(parameters)

    # Filter parameters (applies to *defined* params only; --try params are always shown)
    if args.param:
        filter_names = {n.upper() for n in args.param}
        parameters = {k: v for k, v in parameters.items() if k.upper() in filter_names}
        missing = filter_names - {k.upper() for k in parameters}
        if missing:
            print(f"Warning: parameters not found: {', '.join(sorted(missing))}")
    if args.verified:
        parameters = {k: v for k, v in parameters.items() if v.get("verified", False)}
    if args.unverified:
        parameters = {k: v for k, v in parameters.items() if not v.get("verified", False)}

    # Merge candidate expressions in (override a defined param on name clash).
    if try_params:
        parameters = {**parameters, **try_params}

    # Scope arguments shared by every capture load in this run (date/state/label
    # range + first/last slice). Resolved once so all paths stay consistent.
    scope = {
        "since": since,
        "until": until,
        "state": args.state,
        "label": args.label,
        "first": args.first,
        "last": args.last,
    }
    scoped = any(v is not None for v in scope.values())

    if not parameters and not args.plot:
        # Be capture-aware and split the two cases that used to collapse into one
        # misleading message: filters excluded everything vs. nothing defined yet.
        # (This is a terminating error path, so loading captures here doesn't
        # double-load — the normal path at load_captures() below is only reached
        # when there are parameters to decode.)
        caps = scope_captures(load_captures(args.ecu, args.pid), **scope)
        if defined_params:
            # Params exist for this PID; the active --param/--verified/--unverified
            # filters just excluded them all.
            print(
                f"No parameters match the filter criteria "
                f"({len(defined_params)} defined for {ecu_key} {pid_key})."
            )
        elif caps:
            # Captured but not yet decoded — signpost the byte-level tools.
            print(
                f"{_BOLD}{ecu_key} {pid_key}{_RESET} — no parameters defined yet, "
                f"but {len(caps)} capture(s) exist."
            )
            print(
                f"  {_DIM}·{_RESET} Inspect raw bytes:  "
                f"query-captures.py {ecu_key} {pid_key} --diff"
            )
            print(f"  {_DIM}·{_RESET} Explore signals:    decode.py {ecu_key} {pid_key} --plot")
            print(
                f"  {_DIM}·{_RESET} Test a candidate:   "
                f'decode.py {ecu_key} {pid_key} --try "NAME:unit=EXPR"'
            )
        else:
            # Neither defined nor captured.
            print(f"No parameters defined and no captures found for {ecu_key} {pid_key}.")
        sys.exit(1)

    # Resolve the --corr reference. A reference containing ':' is a cross-signal
    # ECU:PID:PARAM|EXPR loaded from another ECU/PID and time-aligned; otherwise
    # it's a local param name paired by shared payload.
    corr_ref = None
    cross_ref_series = None
    cross_ref_label = None
    if args.corr:
        if ":" in args.corr:
            try:
                cross_ref_series, cross_ref_label = load_cross_ref_series(
                    args.corr, scope=scope, tol_s=args.join_tol
                )
            except ValueError as e:
                print(f"--corr error: {e}", file=sys.stderr)
                return 1
            corr_ref = cross_ref_label
        else:
            corr_ref = resolve_ref(args.corr, list(parameters.keys()))
            if corr_ref is None:
                print(
                    f"--corr reference '{args.corr}' not found. "
                    f"Available: {', '.join(parameters)}"
                )
                return 1

    # Load captures (with any date/state/label/first/last scoping applied).
    captures = scope_captures(load_captures(args.ecu, args.pid), **scope)
    if not captures:
        if scoped:
            print(f"No captures for {ecu_key} PID {pid_key} match the scope filters.")
        else:
            print(f"No captures found for {ecu_key} PID {pid_key}.")
        return 1

    # Decode all captures
    all_results = []
    for cap in captures:
        try:
            wican_bytes = payload_to_wican_bytes(cap["payload"])
        except Exception as e:
            all_results.append(
                {
                    "capture": cap,
                    "decoded": {},
                    "error": f"payload parse error: {e}",
                }
            )
            continue

        decoded = decode_payload(wican_bytes, parameters)
        all_results.append(
            {
                "capture": cap,
                "decoded": decoded,
            }
        )

    # Interactive signal explorer (byte interpretations + params + transforms).
    if args.plot:
        cmd_plot(
            all_results,
            list(parameters.keys()),
            parameters,
            candidate_names,
            corr_ref,
            ecu_key,
            pid_key,
            defined_params=defined_params,
        )
        return 0

    if args.json:
        param_names = list(parameters.keys())
        if args.stats or corr_ref:
            # Aggregate JSON: per-param statistics and/or correlations vs the ref.
            out: dict = {}
            if args.stats:
                out["stats"] = {
                    name: {
                        k: v
                        for k, v in compute_stats(_series(all_results, name)).items()
                        if k != "values"
                    }
                    for name in param_names
                    if _series(all_results, name)
                }
            if corr_ref:
                out["reference"] = corr_ref
                out["correlations"] = {}
                if cross_ref_series is not None:
                    from canlib.align import DEFAULT_JOIN_TOL_S, join_nearest

                    tol = args.join_tol if args.join_tol is not None else DEFAULT_JOIN_TOL_S
                    out["join_tol_s"] = tol
                    for name in param_names:
                        local = _local_series(all_results, name)
                        xs, ys, n = join_nearest(cross_ref_series, local, tol_s=tol)
                        out["correlations"][name] = {"r": _pearson(xs, ys), "n": n}
                else:
                    for name in param_names:
                        if name == corr_ref:
                            continue
                        xs, ys = _paired(all_results, corr_ref, name)
                        out["correlations"][name] = {"r": _pearson(xs, ys), "n": len(xs)}
            json.dump(out, sys.stdout, indent=2, default=str)
            print()
            return 0
        # JSON output — per-capture decoded values (payload-level data lives in
        # query-captures.py; decode.py is parameter/value-centric).
        out = []
        for r in all_results:
            entry = {
                "date": r["capture"]["date"],
                "vehicle_states": r["capture"].get("vehicle_states") or [],
                "file": r["capture"]["file"],
            }
            if r["capture"].get("time"):
                entry["time"] = r["capture"]["time"]
            entry["parameters"] = {}
            for name, d in r["decoded"].items():
                entry["parameters"][name] = {
                    "value": d["value"],
                    "unit": d["unit"],
                    "verified": d["verified"],
                }
                if d.get("error"):
                    entry["parameters"][name]["error"] = d["error"]
            if r.get("error"):
                entry["error"] = r["error"]
            out.append(entry)
        json.dump(out, sys.stdout, indent=2, default=str)
        print()
        return 0

    # Param column order (definition order; --try candidates appended last).
    param_names = list(parameters.keys())

    # Header
    n_verified = sum(1 for p in parameters.values() if p.get("verified", False))
    n_total = len(parameters)
    try_note = (
        f", {_CYAN}{len(candidate_names)} candidate (--try){_RESET}" if candidate_names else ""
    )
    print(
        f"\n{_BOLD}{ecu_key} PID {pid_key}{_RESET} — "
        f"{n_total} parameters ({n_verified} verified, {n_total - n_verified} unverified){try_note}, "
        f"{len(captures)} captures\n"
    )
    scope_banner = _scope_banner(since, until, args.state, args.label, args.first, args.last)
    if scope_banner:
        print(f"  {_DIM}scope: {scope_banner}{_RESET}\n")

    if args.compact:
        print_compact(
            all_results, param_names, parameters, candidate_names, changes_only=args.changes_only
        )
        return 0

    # Default view: parameter value ranges across all captures (validation-focused).
    # --stats and --corr add/replace it with statistics and correlation tables.
    printed = False
    if args.find_mirrors:
        print_mirrors(all_results, bits=args.bits)
        printed = True
    if args.stats:
        if args.group_by == "state":
            print_stats_grouped(all_results, param_names, parameters, candidate_names, "state")
        else:
            print_stats_table(all_results, param_names, parameters, candidate_names)
        printed = True
    if args.discriminate:
        print_discriminate(
            all_results, param_names, parameters, candidate_names, args.discriminate,
            include_bytes=args.bytes,
        )
        printed = True
    if corr_ref:
        print_correlations(
            all_results,
            param_names,
            parameters,
            candidate_names,
            corr_ref,
            cross_ref_series=cross_ref_series,
            cross_ref_label=cross_ref_label,
            tol_s=args.join_tol,
            transform=args.corr_transform,
        )
        printed = True
    if not printed:
        print_value_ranges(all_results, param_names, parameters, candidate_names)
        if sys.stdout.isatty():
            print(
                f"\n  {_DIM}Tip: add --plot to interactively explore these signals "
                f"(byte interpretations, transforms, correlations).{_RESET}"
            )
    return 0
