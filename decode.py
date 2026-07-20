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
  python3 decode.py MCU 2102 --try "TORQUE:Nm=[S12:S13]/100"   # Test a candidate expression
  python3 decode.py MCU 2102 --try "T=[S17:S18]" --corr MCU_MOTOR_RPM  # Validate a candidate by correlation
  python3 decode.py MCU 21F2 --try "X=B9" --try "Y=[S10:S11]"  # Multiple candidates, undefined PID OK
"""

import argparse
import json
import sys
from pathlib import Path

import yaml

# Add parent for canlib imports
sys.path.insert(0, str(Path(__file__).parent))

from bix import _payload_to_wican_frame
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
    """Load all captures matching ECU+PID from capture files."""
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
                cap_ecu = cap.get("ecu", "")
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
    """Compact numeric formatting: integers stay integral, else 2 decimals."""
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
    parser.add_argument("--try", dest="try_expr", action="append", metavar="NAME[:unit]=EXPR",
                        help="Evaluate a candidate expression against captures without editing "
                             "YAML (repeatable; works even if the PID has no params defined yet)")
    args = parser.parse_args()

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
        elif not try_params:
            print(f"PID '{args.pid}' not found for {ecu_key}. Available: {', '.join(sorted(ecu_pids))}")
            sys.exit(1)
    elif not try_params:
        print(f"ECU '{args.ecu}' not found in pids/. Available: {', '.join(sorted(ecu_index))}")
        sys.exit(1)

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

    if not parameters:
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
