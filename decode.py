#!/usr/bin/env python3
"""Decode captured UDS payloads using PID parameter definitions.

Takes an ECU+PID, loads all matching captures, applies WiCAN expressions
from the YAML PID definitions, and reports how each decoded *parameter value*
behaves across the full capture history. Parameter/value-centric and focused
on validating expressions — for payload/byte-level views (hex, byte-diff,
dedup, cross-ECU, dates) use query-captures.py instead.

By default it prints each parameter's value range (min-max, or constant) across
all captures. Use --compact for a chronological one-line-per-capture view, or
--try to test a candidate expression without editing YAML.

Examples:
  python3 decode.py BMS 2101              # Value range of every param across captures
  python3 decode.py BMS 2101 --param SOC_BMS SOC_DISP  # Only specific params
  python3 decode.py IGPM 22BC03           # Decode IGPM DID BC03
  python3 decode.py BMS 2101 --verified   # Only verified parameters
  python3 decode.py BMS 2101 --unverified # Only unverified parameters (validation focus)
  python3 decode.py BMS 2101 --compact    # One line per capture (value evolution)
  python3 decode.py BMS 2101 --json       # JSON (per-capture decoded values)
  python3 decode.py MCU 2102 --stats      # Descriptive stats per param (mean/median/stdev/distinct)
  python3 decode.py MCU 2102 --corr MCU_MOTOR_RPM   # Correlate every param vs a known signal
  python3 decode.py MCU 2102 --plot                      # sweep interpretations, find the signal
  python3 decode.py MCU 2102 --plot --corr MCU_MOTOR_RPM # overlay a known signal + live r
  python3 decode.py MCU 2102 --try "TORQUE:Nm=[S12:S13]/100"   # Test a candidate expression
  python3 decode.py MCU 2102 --try "T=[S17:S18]" --corr MCU_MOTOR_RPM  # Validate a candidate by correlation
  python3 decode.py MCU 21F2 --try "X=B9" --try "Y=[S10:S11]"  # Multiple candidates, undefined PID OK
"""

import argparse
import json
import math
import shutil
import sys
from pathlib import Path

import yaml

# Add parent for canlib imports
sys.path.insert(0, str(Path(__file__).parent))

from bix import _payload_to_wican_frame
from canlib.byteindex import extract_byte_indices
from canlib.expression import evaluate_expression
from canlib.pids import build_ecu_index, load_pids

CAPTURES_DIR = Path(__file__).parent / "captures"
PIDS_DIR = Path(__file__).parent / "pids"

# ANSI colors
_BOLD = "\033[1m"
_DIM = "\033[2m"
_GREEN = "\033[92m"
_YELLOW = "\033[93m"
_RED = "\033[91m"
_CYAN = "\033[96m"
_RESET = "\033[0m"


def load_captures(ecu: str, pid: str) -> list[dict]:
    """Load all captures matching ECU+PID from capture files.

    Capture ``ecu`` fields store the ECU CAN response address (e.g. ``0x7EC``);
    they are resolved to the canonical short name before matching ``ecu``.
    """
    from canlib.ecus import build_rx_index, ecu_name_from_ref

    try:
        rx_index = build_rx_index()
    except Exception:
        rx_index = {}

    entries = []
    for fpath in sorted(CAPTURES_DIR.glob("*.yaml")):
        if fpath.name.startswith(("SCHEMA", "_")):
            continue
        with open(fpath) as f:
            data = yaml.safe_load(f)
        if not data or "sessions" not in data:
            continue
        for session in data["sessions"]:
            date = session.get("date", "")
            label = session.get("label", "")
            state = session.get("state", "")
            for cap in session.get("captures", []):
                cap_ecu = ecu_name_from_ref(cap.get("ecu", ""), rx_index)
                cap_pid = str(cap.get("pid", "")).upper()
                if cap_ecu.upper() != ecu.upper():
                    continue
                if cap_pid != pid.upper():
                    continue
                payload = cap.get("payload")
                if not payload:
                    continue
                entries.append({
                    "file": fpath.name,
                    "date": str(date),
                    "label": label,
                    "state": state,
                    "payload": payload,
                    "notes": cap.get("notes", ""),
                    "time": cap.get("time", ""),
                })
    return entries


def payload_to_wican_bytes(payload_hex: str) -> bytes:
    """Convert raw UDS payload hex to WiCAN frame bytes (with PCI inserted)."""
    payload_hex = payload_hex.replace(" ", "")
    payload_bytes = [int(payload_hex[i:i+2], 16) for i in range(0, len(payload_hex), 2)]
    frame = _payload_to_wican_frame(payload_bytes)
    return bytes(b for b, _ in frame)


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
            f"{_CYAN}»{_RESET}" if is_cand
            else f"{_GREEN}✓{_RESET}" if verified
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
                (r["decoded"][name]["error"] for r in all_results
                 if name in r["decoded"] and r["decoded"][name].get("error")),
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
            print(f"    {mark} {name:<{name_w}}  {format_value(mn, unit):>14}  "
                  f"{_DIM}(constant){_RESET}{try_tag}{warn_str}")
        else:
            print(f"    {mark} {name:<{name_w}}  {format_value(mn, unit):>14} — "
                  f"{format_value(mx, unit)}{try_tag}{warn_str}")
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
    return f"{_GREEN}✓{_RESET}" if parameters[name].get("verified", False) else f"{_YELLOW}?{_RESET}"


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


def resolve_ref(ref: str, param_names: list[str]) -> str | None:
    """Case-insensitively resolve a --corr reference to an actual param name."""
    for n in param_names:
        if n.upper() == ref.upper():
            return n
    return None


def print_correlations(
    all_results: list[dict], param_names: list[str], parameters: dict,
    candidate_names: set[str], ref: str,
) -> None:
    """Pearson correlation of every parameter against ``ref`` across captures.

    The key reverse-engineering lever: correlate a candidate expression against
    a known signal (e.g. a torque guess vs MCU_MOTOR_RPM) to confirm it tracks.
    """
    rows = []
    for name in param_names:
        if name == ref:
            continue
        xs, ys = _paired(all_results, ref, name)
        r = _pearson(xs, ys)
        rows.append((name, r, len(xs)))
    # Strongest absolute correlations first; undefined (None) last.
    rows.sort(key=lambda t: (t[1] is None, -abs(t[1]) if t[1] is not None else 0))

    print(f"  {_BOLD}Correlation vs {ref}{_RESET} {_DIM}(Pearson r){_RESET}")
    for name, r, n in rows:
        mark = _mark_for(name, parameters, candidate_names)
        try_tag = f" {_CYAN}(try){_RESET}" if name in candidate_names else ""
        if r is None:
            print(f"    {mark} {name}{try_tag}  {_DIM}r=n/a  n={n}{_RESET}")
            continue
        color = _GREEN if abs(r) >= 0.7 else _YELLOW if abs(r) >= 0.3 else _DIM
        print(f"    {mark} {name}{try_tag}  {color}r={r:+.3f}{_RESET}  {_DIM}n={n}{_RESET}")
    print()


# ---------------------------------------------------------------------------
# Plot mode (interactive signal exploration)
# ---------------------------------------------------------------------------
#
# Two composable layers, like an ImHex data inspector plus post-processing:
#   1. INTERPRETATION — read raw payload bytes at an offset as a type
#      (u8/i8/u16/.../u64/i64/f16/f32/f64, big/little endian). The "how do I
#      read these bytes" sweep for finding a signal.
#   2. TRANSFORM — post-process the resulting per-capture series
#      (raw/delta/abs/cumsum/normalize/smooth) to expose structure.
# The series (one value per capture) is drawn as a Unicode braille line chart;
# an optional reference parameter can be overlaid with a live Pearson r.

# name, byte width, kind ("int"/"float"), signed (ints only)
INSPECT_TYPES = [
    ("u8", 1, "int", False), ("i8", 1, "int", True),
    ("u16", 2, "int", False), ("i16", 2, "int", True),
    ("u24", 3, "int", False), ("i24", 3, "int", True),
    ("u32", 4, "int", False), ("i32", 4, "int", True),
    ("u64", 8, "int", False), ("i64", 8, "int", True),
    ("f16", 2, "float", True), ("f32", 4, "float", True), ("f64", 8, "float", True),
]

POST_TRANSFORMS = ("raw", "delta", "abs", "cumsum", "normalize", "smooth")

_V_AXIS = "\u2502"   # box vertical
_CORNER = "\u2514"   # box corner
_HLINE = "\u2500"    # box horizontal


def interpret_bytes(frame: bytes, offset: int, spec: tuple, little: bool = False) -> float | None:
    """Read ``frame`` at ``offset`` as one INSPECT_TYPES spec, or None if OOB.

    ``spec`` is ``(name, width, kind, signed)``. Endianness applies to
    multi-byte types; single bytes ignore it.
    """
    _, width, kind, signed = spec
    if offset < 0 or offset + width > len(frame):
        return None
    bs = frame[offset:offset + width]
    if kind == "int":
        order = "little" if (little and width > 1) else "big"
        return float(int.from_bytes(bs, order, signed=signed))
    import struct
    fmt = {2: "e", 4: "f", 8: "d"}[width]
    try:
        return float(struct.unpack(("<" if little else ">") + fmt, bs)[0])
    except (struct.error, ValueError):
        return None


def wican_expr(offset: int, spec: tuple, little: bool = False) -> str | None:
    """Equivalent WiCAN expression for an interpretation, or None if not expressible.

    Big-endian ints map to the ``[Bnn:Bmm]`` / ``[Snn:Smm]`` forms; little-endian
    unsigned ints to a shift-composition; floats and little-endian *signed* ints
    have no direct expression in the WiCAN language.
    """
    _, width, kind, signed = spec
    if kind == "float":
        return None
    c = "S" if signed else "B"
    if width == 1:
        return f"{c}{offset}"
    if not little:
        return f"[{c}{offset}:{c}{offset + width - 1}]"
    if signed:
        return None
    terms = [f"B{offset}"] + [f"(B{offset + k} << {8 * k})" for k in range(1, width)]
    return " | ".join(terms)


def apply_transform(values: list[float], mode: str) -> list[float]:
    """Apply a post-processing transform to a value series (see POST_TRANSFORMS)."""
    if not values or mode == "raw":
        return list(values)
    if mode == "delta":
        return [0.0] + [values[i] - values[i - 1] for i in range(1, len(values))]
    if mode == "abs":
        return [abs(v) for v in values]
    if mode == "cumsum":
        out, run = [], 0.0
        for v in values:
            run += v
            out.append(run)
        return out
    if mode == "normalize":
        return _norm01(values)
    if mode == "smooth":
        w, out = 5, []
        for i in range(len(values)):
            a, b = max(0, i - w // 2), min(len(values), i + w // 2 + 1)
            out.append(sum(values[a:b]) / (b - a))
        return out
    return list(values)


def _norm01(values: list[float]) -> list[float]:
    if not values:
        return []
    lo, hi = min(values), max(values)
    span = hi - lo or 1.0
    return [(v - lo) / span for v in values]


class _Braille:
    """A 2x4-dots-per-cell Unicode braille drawing surface (w x h *cells*)."""

    _DOTS = ((0x01, 0x08), (0x02, 0x10), (0x04, 0x20), (0x40, 0x80))

    def __init__(self, w: int, h: int):
        self.w, self.h = w, h
        self.g = [[0] * w for _ in range(h)]

    def _set(self, px: int, py: int) -> None:
        if 0 <= px < 2 * self.w and 0 <= py < 4 * self.h:
            self.g[py // 4][px // 2] |= self._DOTS[py % 4][px % 2]

    def plot(self, points: list[tuple[int, int]]) -> None:
        """Plot points, connecting consecutive ones with straight segments."""
        prev = None
        for px, py in points:
            if prev is not None:
                x0, y0 = prev
                steps = max(abs(px - x0), abs(py - y0), 1)
                for s in range(steps + 1):
                    self._set(round(x0 + (px - x0) * s / steps),
                              round(y0 + (py - y0) * s / steps))
            else:
                self._set(px, py)
            prev = (px, py)

    def char_grid(self) -> list[list[int]]:
        return [[(0x2800 + c) if c else 0 for c in row] for row in self.g]


def _to_pixels(values: list[float], w: int, h: int, lo: float, hi: float) -> list[tuple[int, int]]:
    span = hi - lo or 1.0
    px_max, py_max = 2 * w - 1, 4 * h - 1
    den = max(len(values) - 1, 1)
    return [(round(i / den * px_max), round((1 - (v - lo) / span) * py_max))
            for i, v in enumerate(values)]


def render_plot(values: list[float], ref: list[float] | None = None,
                width: int = 74, height: int = 16, caption: str | None = None) -> list[str]:
    """Render a braille line chart (list of rows) with a y-axis min/max gutter.

    When ``ref`` is given, both series are normalized to [0,1] and overlaid
    (``values`` bright, ``ref`` dim) so their shapes can be compared. ``caption``
    overrides the default bottom label (used to show the visible x-range).
    """
    if not values:
        return ["  (no data to plot)"]
    overlay = bool(ref)
    if overlay:
        mv, rv, lo, hi = _norm01(values), _norm01(ref), 0.0, 1.0
    else:
        mv, lo, hi = list(values), min(values), max(values)

    main = _Braille(width, height)
    main.plot(_to_pixels(mv, width, height, lo, hi))
    mg = main.char_grid()
    rg = None
    if overlay:
        refg = _Braille(width, height)
        refg.plot(_to_pixels(rv, width, height, lo, hi))
        rg = refg.char_grid()

    gutter = 12
    lines = []
    for r in range(height):
        ylab = _fmt_num(hi) if r == 0 else _fmt_num(lo) if r == height - 1 else ""
        cells = []
        for c in range(width):
            if mg[r][c]:
                cells.append(f"{_GREEN}{chr(mg[r][c])}{_RESET}")
            elif rg and rg[r][c]:
                cells.append(f"{_DIM}{chr(rg[r][c])}{_RESET}")
            else:
                cells.append(" ")
        lines.append(f"{ylab:>{gutter}} {_V_AXIS}{''.join(cells)}")
    lines.append(f"{'':>{gutter}} {_CORNER}{_HLINE * width}")
    if caption is None:
        caption = "normalized 0-1" if overlay else f"{len(values)} captures"
    lines.append(f"{'':>{gutter}}  {_DIM}{caption}{_RESET}")
    return lines


def _window(seq: list, xlo: float, xhi: float) -> tuple[list, int, int]:
    """Slice ``seq`` to the fractional x-window ``[xlo, xhi]``.

    Returns ``(view, i0, i1)`` with ``i0``/``i1`` the resolved integer bounds.
    Fractions (rather than indices) keep the zoom stable as the underlying
    series length changes between offsets/types.
    """
    n = len(seq)
    if n == 0:
        return [], 0, 0
    i0 = max(0, min(n - 1, int(xlo * n)))
    i1 = max(i0 + 1, min(n, round(xhi * n)))
    return seq[i0:i1], i0, i1


def _mapping_for_offset(defined_params: dict, offset: int, width: int,
                        current_expr: str | None) -> tuple[list, list]:
    """Which defined parameters read the byte range ``[offset, offset+width)``.

    Returns ``(exact, overlap)`` lists of ``(name, expression, verified)``:
    ``exact`` matches the current interpretation's expression byte-for-byte;
    ``overlap`` merely reads one of the same bytes. Lets the plot flag bytes
    that are already decoded (and by what) while sweeping.
    """
    cur_bytes = set(range(offset, offset + width))
    norm_cur = current_expr.replace(" ", "") if current_expr else None
    exact, overlap = [], []
    for name, pdef in (defined_params or {}).items():
        expr = (pdef or {}).get("expression", "")
        if not expr:
            continue
        try:
            bs = extract_byte_indices(expr)
        except Exception:
            bs = set()
        if bs & cur_bytes:
            entry = (name, expr, bool((pdef or {}).get("verified", False)))
            if norm_cur and expr.replace(" ", "") == norm_cur:
                exact.append(entry)
            else:
                overlap.append(entry)
    return exact, overlap


def _pci_positions(payload_hex: str) -> set[int]:
    """WiCAN byte indices that are ISO-TP PCI bytes for a payload (role is None)."""
    ph = payload_hex.replace(" ", "")
    try:
        pb = [int(ph[i:i + 2], 16) for i in range(0, len(ph), 2)]
        frame = _payload_to_wican_frame(pb)
    except Exception:
        return set()
    return {i for i, (_, role) in enumerate(frame) if role is None}


def _read_key(fd: int) -> str:
    import os
    return os.read(fd, 16).decode("utf-8", errors="ignore")


def _series_stats_str(values: list[float]) -> str:
    if not values:
        return "n=0"
    return (f"n={len(values)}  min={_fmt_num(min(values))} max={_fmt_num(max(values))} "
            f"mean={_fmt_num(_mean(values))}")


def _cap_ts(cap: dict) -> str:
    """A capture's timestamp as ``YYYY-MM-DD HH:MM:SS`` (date and/or time, trimmed)."""
    return f"{cap.get('date', '')!s} {cap.get('time', '')!s}".strip()


def _view_time_range(caps: list[dict]) -> tuple[str, str]:
    """Earliest/latest timestamp across captures (ISO strings sort chronologically)."""
    tss = [t for t in (_cap_ts(c) for c in caps) if t]
    return (min(tss), max(tss)) if tss else ("", "")


def _cycle_overlay(overlay_ref: str | None, ov_cycle: list) -> str | None:
    """Advance the overlay reference to the next entry in ``ov_cycle`` (wraps).

    ``ov_cycle`` is ``[None, param, param, …]``; returns ``overlay_ref`` unchanged
    when there is nothing to cycle (only the ``None`` entry).
    """
    if len(ov_cycle) <= 1:
        return overlay_ref
    idx = ov_cycle.index(overlay_ref) if overlay_ref in ov_cycle else 0
    return ov_cycle[(idx + 1) % len(ov_cycle)]


def _info_lines(ecu_key: str, pid_key: str, caps_view: list[dict], i0: int,
                total: int, ts_range: str, max_rows: int) -> list[str]:
    """Modal body: list the captures backing the current view (date/state/label/notes/file)."""
    out = [
        f"{_BOLD}{ecu_key} {pid_key}{_RESET}  {_DIM}·  captures in view{_RESET}",
        f"  {_DIM}{len(caps_view)} capture(s)  ·  {ts_range or 'no timestamps'}  ·  "
        f"i/Esc to close{_RESET}",
        "",
    ]
    for n, cap in enumerate(caps_view[:max_rows]):
        state = cap.get("state", "")
        label = cap.get("label", "")
        meta = "  ".join(x for x in [f"[{state}]" if state else "", label] if x)
        out.append(f"  {_CYAN}{i0 + n:>4}{_RESET}  {_BOLD}{_cap_ts(cap) or '?':<20}{_RESET}  "
                   f"{_DIM}{cap.get('file', '')}{_RESET}" + (f"  {meta}" if meta else ""))
        notes = (cap.get("notes", "") or "").replace("\n", " ").strip()
        if notes:
            out.append(f"        {_DIM}{notes[:100]}{_RESET}")
    if len(caps_view) > max_rows:
        out.append(f"  {_DIM}... and {len(caps_view) - max_rows} more — "
                   f"zoom in (+ or ,/.) to narrow the window{_RESET}")
    return out


def cmd_plot(all_results: list[dict], param_names: list[str], parameters: dict,
             candidate_names: set[str], corr_ref: str | None,
             ecu_key: str, pid_key: str, defined_params: dict | None = None) -> None:
    """Interactive signal explorer: sweep byte interpretations / params and plot.

    Byte mode is the ImHex-style inspector (offset x type x endianness over the
    raw payload); param mode plots a defined/--try parameter's decoded series.
    Both feed a post-transform and an optional reference overlay; byte mode also
    shows the equivalent WiCAN expression and flags bytes already mapped by a
    defined parameter. The x-axis can be zoomed/panned. Falls back to a single
    static chart when stdin/stdout is not a TTY.
    """
    defined_params = defined_params or {}
    # Raw WiCAN frames per capture (offset space matches Bnn / expressions).
    frames: list[bytes | None] = []
    for r in all_results:
        payload = r["capture"].get("payload")
        try:
            frames.append(payload_to_wican_bytes(payload) if payload else None)
        except Exception:
            frames.append(None)
    valid = [f for f in frames if f]
    longest_payload = max((r["capture"]["payload"] for r in all_results
                           if r["capture"].get("payload")), key=len, default="")
    pci = _pci_positions(longest_payload)
    max_off = (max((len(f) for f in valid), default=1)) - 1

    plottable_params = [n for n in param_names
                        if len([1 for r in all_results
                                if r["decoded"].get(n, {}).get("value") is not None]) >= 2]

    # Overlay reference is selectable at runtime (cycled with `o`), seeded by
    # --corr when given. Any numeric param can be overlaid — no --corr required.
    ov_cycle = [None, *dict.fromkeys(([corr_ref] if corr_ref else []) + plottable_params)]

    if not valid and not plottable_params:
        print("  Nothing to plot (no decodable payloads or numeric params).")
        return

    # ---- state ----
    mode = "bytes" if valid else "param"
    offset = min(max_off, 3)           # skip PCI/SID/echo by default
    ti = 0                              # INSPECT_TYPES index
    little = False
    tmode = "raw"                       # post-transform
    pi = 0                              # param index (param mode)
    overlay_ref = corr_ref              # overlay reference param (None = off)
    xlo, xhi = 0.0, 1.0                 # fractional x-axis window (zoom/pan)
    show_info = False                   # captures-in-view modal

    def frame_lines() -> list[str]:
        spec = INSPECT_TYPES[ti]
        warn = ""
        map_line = None
        if mode == "bytes":
            per_cap = [interpret_bytes(f, offset, spec, little) if f else None for f in frames]
            expr = wican_expr(offset, spec, little)
            width = spec[1]
            if any((offset + k) in pci for k in range(width)):
                warn = "crosses PCI byte — likely garbage"
            endian = "" if width == 1 else ("  LE" if little else "  BE")
            src = f"B{offset} as {spec[0]}{endian}"
            expr_line = f"expr: {expr}" if expr else "expr: (no direct WiCAN expression)"
            # Feature: flag bytes already mapped by a defined parameter.
            exact, overlap = _mapping_for_offset(defined_params, offset, width, expr)
            if exact:
                n_, e_, v_ = exact[0]
                mk = f"{_GREEN}✓{_RESET}" if v_ else f"{_YELLOW}?{_RESET}"
                map_line = f"  {_GREEN}= mapped: {n_}{_RESET} {mk} {_DIM}({e_}){_RESET}"
            elif overlap:
                shown = "  ".join(f"{n_} {_DIM}({e_}){_RESET}" for n_, e_, _ in overlap[:3])
                more = f" +{len(overlap) - 3}" if len(overlap) > 3 else ""
                map_line = f"  {_YELLOW}~ reads B{offset}:{_RESET} {shown}{more}"
            else:
                map_line = f"  {_DIM}unmapped{_RESET}"
        else:
            if not plottable_params:
                return [f"{_BOLD}{ecu_key} {pid_key}{_RESET}",
                        "  No numeric parameters to plot — press m for byte mode."]
            name = plottable_params[pi % len(plottable_params)]
            per_cap = [r["decoded"].get(name, {}).get("value") for r in all_results]
            expr_line = f"expr: {parameters.get(name, {}).get('expression', '')}"
            src = name

        # Overlay reference resolved from runtime state (cycled with `o`).
        overlay = overlay_ref is not None
        ref_per_cap = ([r["decoded"].get(overlay_ref, {}).get("value") for r in all_results]
                       if overlay else None)

        # Drop missing (None) and non-finite (NaN/Inf) values — float byte
        # interpretations routinely yield NaN/Inf, which can't be plotted or
        # averaged. Keep each retained value's capture aligned for the modal.
        caps_all = [r["capture"] for r in all_results]
        if overlay and ref_per_cap is not None:
            triples = [(cap, rf, cv)
                       for cap, rf, cv in zip(caps_all, ref_per_cap, per_cap, strict=True)
                       if rf is not None and cv is not None
                       and math.isfinite(rf) and math.isfinite(cv)]
            caps_full = [t[0] for t in triples]
            ref_full = [t[1] for t in triples]
            cur_full = apply_transform([t[2] for t in triples], tmode)
        else:
            kept = [(cap, v) for cap, v in zip(caps_all, per_cap, strict=True)
                    if v is not None and math.isfinite(v)]
            caps_full = [k[0] for k in kept]
            ref_full = None
            cur_full = apply_transform([k[1] for k in kept], tmode)

        # Apply the x-axis window (zoom/pan), keeping ref + captures aligned.
        series, i0, i1 = _window(cur_full, xlo, xhi)
        caps_view = caps_full[i0:i1]
        refseries = ref_full[i0:i1] if ref_full is not None else None

        # Date/time span of the *visible* window (accounts for zoom).
        lo_ts, hi_ts = _view_time_range(caps_view)
        ts_range = f"{lo_ts} → {hi_ts}" if lo_ts else "no timestamps"

        total = len(cur_full)

        # Captures-in-view modal takes over the frame when toggled.
        if show_info:
            max_rows = max(4, shutil.get_terminal_size((80, 24)).lines - 8)
            return _info_lines(ecu_key, pid_key, caps_view, i0, total, ts_range, max_rows)

        if overlay and refseries is not None:
            r = _pearson(refseries, series)
            rstr = f"  {_CYAN}r={r:+.3f} vs {overlay_ref}{_RESET}" if r is not None \
                else f"  {_DIM}r=n/a vs {overlay_ref}{_RESET}"
        else:
            rstr = ""

        zoomed = (i0, i1) != (0, total)
        caption = (f"captures {i0}-{i1 - 1} of {total}" if total else "no data") \
            + f"  ·  {ts_range}" + ("  (zoomed)" if zoomed else "") \
            + ("  · normalized 0-1" if overlay else "")

        out = [
            f"{_BOLD}{ecu_key} {pid_key}{_RESET}  {_DIM}·  {mode} mode{_RESET}",
            f"  {_CYAN}{src}{_RESET}   {_DIM}{expr_line}{_RESET}",
        ]
        if map_line:
            out.append(map_line)
        out.append(
            f"  transform={_YELLOW}{tmode}{_RESET}  {_series_stats_str(series)}{rstr}"
            + (f"   {_RED}\u26a0 {warn}{_RESET}" if warn else "")
        )
        out.append("")
        out.extend(render_plot(series, ref=refseries if overlay else None, caption=caption))
        return out

    # Non-interactive: print one static frame and return.
    if not (sys.stdin.isatty() and sys.stdout.isatty()):
        print("\n".join(frame_lines()))
        print("  (interactive --plot needs a TTY for navigation)")
        return

    import termios
    import tty

    fd = sys.stdin.fileno()
    old = termios.tcgetattr(fd)
    sys.stdout.write("\033[?1049h\033[?25l")
    sys.stdout.flush()
    status = ""
    try:
        tty.setcbreak(fd)
        while True:
            sys.stdout.write("\033[2J\033[H")
            sys.stdout.write("\r\n".join(frame_lines()))
            common = "  +/- zoom · ,/. pan · 0 reset-x · f transform · o overlay · i captures · q quit"
            hint = ("←/→ offset · t/T type · e endian · m param" + common
                    if mode == "bytes"
                    else "←/→ param · m bytes" + common)
            sys.stdout.write(f"\r\n\r\n  {_DIM}{hint}{_RESET}\r\n")
            if status:
                sys.stdout.write(f"  {_YELLOW}{status}{_RESET}\r\n")
            sys.stdout.flush()

            status = ""
            k = _read_key(fd)
            if k in ("i", "I"):                         # toggle captures-in-view modal
                show_info = not show_info
            elif k in ("q", "Q", "\x03"):
                break
            elif k in ("\x1b", "\x1b\x1b"):             # Esc closes the modal, else quits
                if show_info:
                    show_info = False
                else:
                    break
            elif k in ("\x1b[D", "h"):
                if mode == "bytes":
                    offset = max(0, offset - 1)
                elif plottable_params:
                    pi = (pi - 1) % len(plottable_params)
            elif k in ("\x1b[C", "l"):
                if mode == "bytes":
                    offset = min(max_off, offset + 1)
                elif plottable_params:
                    pi = (pi + 1) % len(plottable_params)
            elif k == "t":
                ti = (ti + 1) % len(INSPECT_TYPES)
            elif k == "T":
                ti = (ti - 1) % len(INSPECT_TYPES)
            elif k == "e":
                little = not little
            elif k == "f":
                tmode = POST_TRANSFORMS[(POST_TRANSFORMS.index(tmode) + 1) % len(POST_TRANSFORMS)]
            elif k == "m":
                mode = "param" if mode == "bytes" else "bytes"
            elif k in ("o", "O"):                       # cycle overlay reference param
                if len(ov_cycle) > 1:
                    overlay_ref = _cycle_overlay(overlay_ref, ov_cycle)
                    status = f"overlay: {overlay_ref}" if overlay_ref else "overlay: off"
                else:
                    status = "no numeric param to overlay (define one or use --try)"
            elif k in ("+", "="):                       # zoom in (halve window)
                c, half = (xlo + xhi) / 2, (xhi - xlo) / 4
                if (xhi - xlo) > 0.02:
                    xlo, xhi = max(0.0, c - half), min(1.0, c + half)
            elif k in ("-", "_"):                       # zoom out (double window)
                c, half = (xlo + xhi) / 2, (xhi - xlo)
                xlo, xhi = max(0.0, c - half), min(1.0, c + half)
            elif k in (",", "<"):                       # pan left
                d = min(xlo, 0.1 * (xhi - xlo))
                xlo, xhi = xlo - d, xhi - d
            elif k in (".", ">"):                       # pan right
                d = min(1.0 - xhi, 0.1 * (xhi - xlo))
                xlo, xhi = xlo + d, xhi + d
            elif k == "0":                              # reset x-window
                xlo, xhi = 0.0, 1.0
    finally:
        termios.tcsetattr(fd, termios.TCSADRAIN, old)
        sys.stdout.write("\033[?25h\033[?1049l")
        sys.stdout.flush()


def main():
    parser = argparse.ArgumentParser(
        description="Decode captured UDS payloads using PID parameter definitions.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__.split("Examples:")[1] if "Examples:" in __doc__ else "",
    )
    parser.add_argument("ecu", help="ECU name (e.g., BMS, IGPM, BCM)")
    parser.add_argument("pid", help="PID code (e.g., 2101, 22BC03)")
    parser.add_argument("--param", nargs="+", metavar="NAME",
                        help="Show only specific parameters")
    parser.add_argument("--verified", action="store_true",
                        help="Show only verified parameters")
    parser.add_argument("--unverified", action="store_true",
                        help="Show only unverified parameters")
    parser.add_argument("--json", action="store_true",
                        help="Output as JSON (per-capture decoded values)")
    parser.add_argument("--compact", action="store_true",
                        help="One line per capture (chronological param=value pairs)")
    parser.add_argument("--stats", action="store_true",
                        help="Descriptive statistics per param (n, distinct, mean, median, stdev)")
    parser.add_argument("--corr", metavar="PARAM",
                        help="Correlate every param (incl. --try) against PARAM (Pearson r)")
    parser.add_argument("--plot", action="store_true",
                        help="Interactive signal explorer: sweep byte interpretations "
                             "(u8/i16/f32/... and endianness) and params, plot across captures, "
                             "apply transforms (delta/abs/normalize/...), zoom/pan the x-axis, "
                             "overlay a --corr signal, and flag bytes already mapped by a param")
    parser.add_argument("--try", dest="try_expr", action="append", metavar="NAME[:unit]=EXPR",
                        help="Evaluate a candidate expression against captures without editing "
                             "YAML (repeatable; works even if the PID has no params defined yet)")
    args = parser.parse_args()

    # --plot and --try tolerate a not-yet-defined ECU/PID (raw byte inspection).
    tolerate_missing = bool(args.try_expr) or args.plot

    # Build any candidate expressions from --try (validated early for a clean error).
    try:
        try_params = build_try_params(args.try_expr) if args.try_expr else {}
    except ValueError as e:
        print(f"Error: {e}")
        sys.exit(1)
    candidate_names = set(try_params)

    # Load PID definitions
    pids_data = load_pids(PIDS_DIR)
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
            print(f"PID '{args.pid}' not found for {ecu_key}. Available: {', '.join(sorted(ecu_pids))}")
            sys.exit(1)
    elif not tolerate_missing:
        print(f"ECU '{args.ecu}' not found in pids/. Available: {', '.join(sorted(ecu_index))}")
        sys.exit(1)

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

    if not parameters and not args.plot:
        print("No parameters match the filter criteria.")
        sys.exit(1)

    # Resolve the --corr reference against the (post-merge) parameter set.
    corr_ref = None
    if args.corr:
        corr_ref = resolve_ref(args.corr, list(parameters.keys()))
        if corr_ref is None:
            print(f"--corr reference '{args.corr}' not found. "
                  f"Available: {', '.join(parameters)}")
            sys.exit(1)

    # Load captures
    captures = load_captures(args.ecu, args.pid)
    if not captures:
        print(f"No captures found for {ecu_key} PID {pid_key}.")
        sys.exit(1)

    # Decode all captures
    all_results = []
    for cap in captures:
        try:
            wican_bytes = payload_to_wican_bytes(cap["payload"])
        except Exception as e:
            all_results.append({
                "capture": cap,
                "decoded": {},
                "error": f"payload parse error: {e}",
            })
            continue

        decoded = decode_payload(wican_bytes, parameters)
        all_results.append({
            "capture": cap,
            "decoded": decoded,
        })

    # Interactive signal explorer (byte interpretations + params + transforms).
    if args.plot:
        cmd_plot(all_results, list(parameters.keys()), parameters,
                 candidate_names, corr_ref, ecu_key, pid_key, defined_params=defined_params)
        return

    if args.json:
        param_names = list(parameters.keys())
        if args.stats or corr_ref:
            # Aggregate JSON: per-param statistics and/or correlations vs the ref.
            out: dict = {}
            if args.stats:
                out["stats"] = {
                    name: {k: v for k, v in compute_stats(_series(all_results, name)).items()
                           if k != "values"}
                    for name in param_names if _series(all_results, name)
                }
            if corr_ref:
                out["reference"] = corr_ref
                out["correlations"] = {}
                for name in param_names:
                    if name == corr_ref:
                        continue
                    xs, ys = _paired(all_results, corr_ref, name)
                    out["correlations"][name] = {"r": _pearson(xs, ys), "n": len(xs)}
            json.dump(out, sys.stdout, indent=2, default=str)
            print()
            return
        # JSON output — per-capture decoded values (payload-level data lives in
        # query-captures.py; decode.py is parameter/value-centric).
        out = []
        for r in all_results:
            entry = {
                "date": r["capture"]["date"],
                "state": r["capture"]["state"],
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
        return

    # Param column order (definition order; --try candidates appended last).
    param_names = list(parameters.keys())

    # Header
    n_verified = sum(1 for p in parameters.values() if p.get("verified", False))
    n_total = len(parameters)
    try_note = f", {_CYAN}{len(candidate_names)} candidate (--try){_RESET}" if candidate_names else ""
    print(f"\n{_BOLD}{ecu_key} PID {pid_key}{_RESET} — "
          f"{n_total} parameters ({n_verified} verified, {n_total - n_verified} unverified){try_note}, "
          f"{len(captures)} captures\n")

    if args.compact:
        # One line per capture (chronological value evolution). Opt-in — for
        # payload/byte-level views across captures use query-captures.py --diff.
        for r in all_results:
            cap = r["capture"]
            ts = cap.get("time") or cap["date"]
            state_str = f" [{cap['state']}]" if cap["state"] else ""
            parts = []
            for name in param_names:
                d = r["decoded"].get(name)
                if not d:
                    continue
                val = format_value(d["value"], d["unit"])
                color = _CYAN if name in candidate_names else (_GREEN if d["verified"] else _YELLOW)
                parts.append(f"{color}{name}{_RESET}={val}")
            line = " ".join(parts)
            if r.get("error"):
                line = f"{_RED}{r['error']}{_RESET}"
            print(f"  {_DIM}{ts}{state_str}{_RESET}  {line}")
        print()
        return

    # Default view: parameter value ranges across all captures (validation-focused).
    # --stats and --corr add/replace it with statistics and correlation tables.
    printed = False
    if args.stats:
        print_stats_table(all_results, param_names, parameters, candidate_names)
        printed = True
    if corr_ref:
        print_correlations(all_results, param_names, parameters, candidate_names, corr_ref)
        printed = True
    if not printed:
        print_value_ranges(all_results, param_names, parameters, candidate_names)


if __name__ == "__main__":
    main()
