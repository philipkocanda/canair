#!/usr/bin/env python3
"""Decode captured UDS payloads using PID parameter definitions.

Takes an ECU+PID, loads all matching captures, applies WiCAN expressions
from the YAML PID definitions, and shows decoded parameter values across
all historical captures. Useful for validating expressions and spotting
anomalies.

Examples:
  python3 decode.py BMS 2101              # Decode all BMS 2101 captures
  python3 decode.py BMS 2101 --param SOC_BMS SOC_DISP  # Only specific params
  python3 decode.py IGPM 22BC03           # Decode IGPM DID BC03
  python3 decode.py BMS 2101 --verified   # Only verified parameters
  python3 decode.py BMS 2101 --unverified # Only unverified parameters
  python3 decode.py BMS 2101 --json       # JSON output
  python3 decode.py BMS 2101 --raw        # Also show raw payload hex
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
from canlib.pids import load_pids, build_ecu_index

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
                        help="Output as JSON")
    parser.add_argument("--raw", action="store_true",
                        help="Also show raw payload hex per capture")
    parser.add_argument("--compact", action="store_true",
                        help="One line per capture (param=value pairs)")
    args = parser.parse_args()

    # Load PID definitions
    pids_data = load_pids(PIDS_DIR)
    ecu_index = build_ecu_index(pids_data)

    ecu_key = args.ecu.upper()
    pid_key = args.pid.upper()

    if ecu_key not in ecu_index:
        print(f"ECU '{args.ecu}' not found in pids/. Available: {', '.join(sorted(ecu_index))}")
        sys.exit(1)

    ecu_pids = ecu_index[ecu_key]["pids"]
    if pid_key not in ecu_pids:
        print(f"PID '{args.pid}' not found for {ecu_key}. Available: {', '.join(sorted(ecu_pids))}")
        sys.exit(1)

    parameters = ecu_pids[pid_key]["parameters"]

    # Filter parameters
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

    if not parameters:
        print("No parameters match the filter criteria.")
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
        # JSON output
        out = []
        for r in all_results:
            entry = {
                "date": r["capture"]["date"],
                "state": r["capture"]["state"],
                "file": r["capture"]["file"],
            }
            if r["capture"].get("time"):
                entry["time"] = r["capture"]["time"]
            if args.raw:
                entry["payload"] = r["capture"]["payload"]
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

    # Sorted param names for consistent column order
    param_names = list(parameters.keys())

    # Header
    n_verified = sum(1 for p in parameters.values() if p.get("verified", False))
    n_total = len(parameters)
    print(f"\n{_BOLD}{ecu_key} PID {pid_key}{_RESET} — "
          f"{n_total} parameters ({n_verified} verified, {n_total - n_verified} unverified), "
          f"{len(captures)} captures\n")

    if args.compact:
        # One line per capture
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
                color = _GREEN if d["verified"] else _YELLOW
                parts.append(f"{color}{name}{_RESET}={val}")
            line = " ".join(parts)
            if r.get("error"):
                line = f"{_RED}{r['error']}{_RESET}"
            print(f"  {_DIM}{ts}{state_str}{_RESET}  {line}")
        print()
        return

    # Full table output — one section per capture
    for i, r in enumerate(all_results):
        cap = r["capture"]
        ts = cap.get("time") or cap["date"]
        state_str = f"  state={cap['state']}" if cap["state"] else ""
        label_str = f"  {_DIM}{cap['label']}{_RESET}" if cap.get("label") else ""
        file_str = f"  {_DIM}({cap['file']}){_RESET}"

        print(f"  {_CYAN}[{i+1}/{len(all_results)}]{_RESET} {_BOLD}{ts}{_RESET}{state_str}{label_str}{file_str}")

        if args.raw:
            payload = cap["payload"].replace(" ", "")
            # Format as spaced hex
            spaced = " ".join(payload[j:j+2] for j in range(0, len(payload), 2))
            print(f"         {_DIM}{spaced}{_RESET}")

        if r.get("error"):
            print(f"         {_RED}{r['error']}{_RESET}")
            print()
            continue

        # Find max param name width
        name_w = max(len(n) for n in param_names) if param_names else 10

        for name in param_names:
            d = r["decoded"].get(name)
            if not d:
                continue

            val_str = format_value(d["value"], d["unit"])
            verified_mark = f"{_GREEN}✓{_RESET}" if d["verified"] else f"{_YELLOW}?{_RESET}"

            warning = ""
            rng = check_range(d["value"], d)
            if rng:
                warning = f"  {_RED}⚠ {rng}{_RESET}"
            if d.get("error"):
                val_str = f"{_RED}ERROR: {d['error']}{_RESET}"

            expr_str = f"{_DIM}{d['expression']}{_RESET}"
            print(f"    {verified_mark} {name:<{name_w}}  {val_str:>14}  {expr_str}{warning}")

        if cap.get("notes"):
            notes_preview = cap["notes"][:100].replace("\n", " ")
            print(f"    {_DIM}note: {notes_preview}{_RESET}")
        print()

    # Summary: value ranges across all captures
    if len(all_results) > 1:
        print(f"  {_BOLD}Value ranges across {len(all_results)} captures:{_RESET}")
        name_w = max(len(n) for n in param_names) if param_names else 10
        for name in param_names:
            values = []
            for r in all_results:
                d = r["decoded"].get(name)
                if d and d["value"] is not None:
                    values.append(d["value"])
            if not values:
                continue
            mn, mx = min(values), max(values)
            verified = parameters[name].get("verified", False)
            mark = f"{_GREEN}✓{_RESET}" if verified else f"{_YELLOW}?{_RESET}"
            unit = parameters[name].get("unit", "")
            if mn == mx:
                print(f"    {mark} {name:<{name_w}}  {format_value(mn, unit):>14}  (constant)")
            else:
                print(f"    {mark} {name:<{name_w}}  {format_value(mn, unit):>14} — {format_value(mx, unit)}")
        print()


if __name__ == "__main__":
    main()
