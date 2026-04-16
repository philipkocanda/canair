#!/usr/bin/env python3
"""Decode captured UDS response payloads using WiCAN expression definitions.

Reads captures.yaml (raw ISO-TP payloads) and ioniq-2017-pids.yaml (parameter
definitions with WiCAN expressions), evaluates each expression against matching
captures, and prints decoded values.

Usage:
    python3 decode-captures.py                  # decode all captures
    python3 decode-captures.py --session 2025-08-04
    python3 decode-captures.py --ecu BMS
    python3 decode-captures.py --pid 2101
    python3 decode-captures.py --param SOC_BMS
    python3 decode-captures.py --raw 6101ffffffffbd17aa... --expr "B09/2"
    python3 decode-captures.py --hexdump        # show annotated hex dump per capture
"""

import argparse
import glob
import math
import os
import re
import sys

import yaml

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
PIDS_FILE = os.path.join(SCRIPT_DIR, "ioniq-2017-pids.yaml")
CAPTURES_FILE = os.path.join(SCRIPT_DIR, "captures.yaml")
CAPTURES_DIR = os.path.join(SCRIPT_DIR, "captures")
ECUS_FILE = os.path.join(SCRIPT_DIR, "ecus.yaml")


# ─── WiCAN Expression Evaluator ──────────────────────────────────────────────
# Faithful Python port of wican-fw/main/expression_parser.c evaluate_expression()

def evaluate_expression(expression: str, data: bytes, V: float = 0.0) -> float:
    """Evaluate a WiCAN expression against a byte array.

    Supported syntax:
        Bn      — unsigned byte at index n
        Sn      — signed byte at index n (int8)
        Bn:m    — bit m of byte n (0=LSB)
        [Bn:Bm] — big-endian unsigned multi-byte (up to 8 bytes)
        [Sn:Sm] — big-endian signed multi-byte (auto-sized: 8/16/32/64-bit)
        V       — external value parameter (default 0)
        + - * / — arithmetic
        << >>   — bit shift
        & | ^   — bitwise AND, OR, XOR
        ( )     — grouping
        numeric — integer or float literals
    """
    operand_stack: list[float] = []
    operator_stack: list[str] = []

    def precedence(op: str) -> int:
        if op in ('|', '^'):
            return 1
        if op == '&':
            return 2
        if op in ('<<', '>>'):
            return 3
        if op in ('+', '-'):
            return 4
        if op in ('*', '/'):
            return 5
        return 0

    def apply_op(op: str, a: float, b: float) -> float:
        if op == '+':
            return a + b
        if op == '-':
            return a - b
        if op == '*':
            return a * b
        if op == '/':
            if b == 0:
                raise ZeroDivisionError(f"Division by zero in expression: {expression}")
            return a / b
        if op == '&':
            return float(int(a) & int(b))
        if op == '|':
            return float(int(a) | int(b))
        if op == '^':
            return float(int(a) ^ int(b))
        if op == '<<':
            return float(int(a) << int(b))
        if op == '>>':
            return float(int(a) >> int(b))
        raise ValueError(f"Unknown operator: {op}")

    def process_pending(min_prec: int):
        while operator_stack and operator_stack[-1] != '(' and precedence(operator_stack[-1]) >= min_prec:
            op = operator_stack.pop()
            b = operand_stack.pop()
            a = operand_stack.pop()
            operand_stack.append(apply_op(op, a, b))

    i = 0
    expr = expression.strip()

    while i < len(expr):
        ch = expr[i]

        # Whitespace
        if ch == ' ':
            i += 1
            continue

        # Numeric literal
        if ch.isdigit() or (ch == '.' and i + 1 < len(expr) and expr[i + 1].isdigit()):
            j = i
            while j < len(expr) and (expr[j].isdigit() or expr[j] == '.'):
                j += 1
            operand_stack.append(float(expr[i:j]))
            i = j
            continue

        # V (external value)
        if ch == 'V' and (i + 1 >= len(expr) or not expr[i + 1].isalnum()):
            operand_stack.append(V)
            i += 1
            continue

        # Multi-byte range: [Bn:Bm] or [Sn:Sm]
        if ch == '[':
            m_unsigned = re.match(r'\[B(\d+):B(\d+)\]', expr[i:])
            if m_unsigned:
                start_idx = int(m_unsigned.group(1))
                end_idx = int(m_unsigned.group(2))
                value = 0
                for j in range(start_idx, end_idx + 1):
                    shift = (end_idx - j) * 8
                    value |= (data[j] << shift)
                operand_stack.append(float(value))
                i += m_unsigned.end()
                continue

            m_signed = re.match(r'\[S(\d+):S(\d+)\]', expr[i:])
            if m_signed:
                start_idx = int(m_signed.group(1))
                end_idx = int(m_signed.group(2))
                span = end_idx - start_idx
                raw = 0
                for j in range(start_idx, end_idx + 1):
                    shift = (end_idx - j) * 8
                    raw |= (data[j] << shift)
                # Sign-extend based on byte count (matching firmware logic)
                if span == 0:
                    value = raw if raw < 128 else raw - 256
                elif span == 1:
                    value = raw if raw < 32768 else raw - 65536
                elif span <= 3:
                    value = raw if raw < 2147483648 else raw - 4294967296
                else:
                    value = raw if raw < (1 << 63) else raw - (1 << 64)
                operand_stack.append(float(value))
                i += m_signed.end()
                continue

            raise ValueError(f"Invalid array syntax at position {i}: {expr[i:]}")

        # Unsigned byte: Bn or Bn:bit
        if ch == 'B':
            i += 1
            idx = 0
            while i < len(expr) and expr[i].isdigit():
                idx = idx * 10 + int(expr[i])
                i += 1
            if i < len(expr) and expr[i] == ':':
                i += 1
                bit = int(expr[i])
                i += 1
                operand_stack.append(float((data[idx] >> bit) & 1))
            else:
                operand_stack.append(float(data[idx]))
            continue

        # Signed byte: Sn
        if ch == 'S':
            i += 1
            idx = 0
            while i < len(expr) and expr[i].isdigit():
                idx = idx * 10 + int(expr[i])
                i += 1
            val = data[idx]
            operand_stack.append(float(val if val < 128 else val - 256))
            continue

        # Parentheses
        if ch == '(':
            operator_stack.append('(')
            i += 1
            continue

        if ch == ')':
            while operator_stack and operator_stack[-1] != '(':
                op = operator_stack.pop()
                b = operand_stack.pop()
                a = operand_stack.pop()
                operand_stack.append(apply_op(op, a, b))
            if operator_stack and operator_stack[-1] == '(':
                operator_stack.pop()
            i += 1
            continue

        # Operators
        if ch in ('+', '-', '*', '/', '&', '|', '^'):
            process_pending(precedence(ch))
            operator_stack.append(ch)
            i += 1
            continue

        if ch == '<' and i + 1 < len(expr) and expr[i + 1] == '<':
            process_pending(precedence('<<'))
            operator_stack.append('<<')
            i += 2
            continue

        if ch == '>' and i + 1 < len(expr) and expr[i + 1] == '>':
            process_pending(precedence('>>'))
            operator_stack.append('>>')
            i += 2
            continue

        raise ValueError(f"Invalid character '{ch}' at position {i} in expression: {expression}")

    # Final reduction
    while operator_stack:
        op = operator_stack.pop()
        b = operand_stack.pop()
        a = operand_stack.pop()
        operand_stack.append(apply_op(op, a, b))

    if len(operand_stack) != 1:
        raise ValueError(f"Expression did not reduce to single value: {expression}")

    return operand_stack[0]


# ─── Data Loading ─────────────────────────────────────────────────────────────

def load_pids(path: str = PIDS_FILE) -> dict:
    with open(path) as f:
        return yaml.safe_load(f)


def load_captures(path: str = None) -> dict:
    """Load captures from captures/ directory or a single file."""
    if path and os.path.isfile(path):
        with open(path) as f:
            return yaml.safe_load(f)
    # Load all YAML files from captures/ directory
    captures_dir = path if path and os.path.isdir(path) else CAPTURES_DIR
    all_sessions = []
    for fpath in sorted(glob.glob(os.path.join(captures_dir, "*.yaml"))):
        if os.path.basename(fpath) == "SCHEMA.yaml":
            continue
        with open(fpath) as f:
            data = yaml.safe_load(f)
        if data and "sessions" in data:
            all_sessions.extend(data["sessions"])
    return {"sessions": all_sessions}


def load_ecus_lookup() -> dict:
    """Load ECU name -> tx_id lookup from ecus.yaml."""
    with open(ECUS_FILE) as f:
        data = yaml.safe_load(f)
    result = {}
    for tx_id, info in data.get("ecus", {}).items():
        if isinstance(tx_id, str) and tx_id.startswith("0x"):
            tx_id = int(tx_id, 16)
        result[info["name"].upper()] = int(tx_id)
    # Add aliases
    result["BCM/TPMS"] = result.get("BCM", result.get("BCM/TPMS", 0))
    return result


def hex_to_bytes(hex_str: str) -> bytes:
    return bytes.fromhex(hex_str)


def find_pid_definition(pids_data: dict, ecu_tx: int, pid: str) -> tuple:
    """Find PID definition matching an ECU tx_id and PID.

    Returns (ecu_name, pid_def, parameters) or (None, None, None).
    """
    tx_hex = f"0x{ecu_tx:X}" if isinstance(ecu_tx, int) else ecu_tx

    for ecu_label, ecu_def in pids_data.get("ecus", {}).items():
        ecu_tx_id = ecu_def.get("tx_id")
        # Normalize to comparable format
        if isinstance(ecu_tx_id, int):
            ecu_tx_val = ecu_tx_id
        elif isinstance(ecu_tx_id, str):
            ecu_tx_val = int(ecu_tx_id, 16) if ecu_tx_id.startswith("0x") else int(ecu_tx_id)
        else:
            continue

        if isinstance(ecu_tx, str):
            ecu_tx_int = int(ecu_tx, 16) if ecu_tx.startswith("0x") else int(ecu_tx)
        else:
            ecu_tx_int = ecu_tx

        if ecu_tx_val != ecu_tx_int:
            continue

        for pid_key, pid_def in ecu_def.get("pids", {}).items():
            if str(pid_key).upper() == str(pid).upper():
                return ecu_label, pid_def, pid_def.get("parameters", {})

    return None, None, None


# ─── Decode + Display ─────────────────────────────────────────────────────────

def decode_capture(payload_bytes: bytes, parameters: dict) -> list:
    """Evaluate all parameter expressions against a payload.

    Returns list of (name, value, unit, expression, error).
    """
    results = []
    for param_name, param_def in parameters.items():
        expr = param_def.get("expression", "")
        unit = param_def.get("unit", "")
        if not expr:
            continue
        try:
            value = evaluate_expression(expr, payload_bytes)
            # Round to 2 decimal places (matching firmware: round(result * 100) / 100)
            value = round(value * 100) / 100
            results.append((param_name, value, unit, expr, None))
        except Exception as e:
            results.append((param_name, None, unit, expr, str(e)))
    return results


def format_value(value: float, unit: str) -> str:
    if value is None:
        return "ERROR"
    if value == int(value):
        return f"{int(value)} {unit}".strip()
    return f"{value:.2f} {unit}".strip()


def print_decoded(capture: dict, ecu_label: str, parameters: dict, results: list):
    """Print decoded results for a single capture."""
    ecu_name = capture.get("ecu") or capture.get("ecu_name", "?")
    pid = capture.get("pid", "?")
    tx = capture.get("ecu_tx")
    tx_str = f"0x{tx:03X}" if isinstance(tx, int) else ""

    print(f"\n{'─' * 72}")
    print(f"  {ecu_name} ({ecu_label}) — PID {pid}" + (f" — TX {tx_str}" if tx_str else ""))
    if capture.get("notes"):
        print(f"  Note: {capture['notes']}")
    print(f"{'─' * 72}")

    # Column widths
    max_name = max((len(r[0]) for r in results), default=10)
    max_val = max((len(format_value(r[1], r[2])) for r in results), default=10)

    for name, value, unit, expr, error in results:
        val_str = format_value(value, unit)
        if error:
            print(f"  {name:<{max_name}}  {'ERROR':<{max_val}}  {expr:<30}  !! {error}")
        else:
            verified = "  "
            print(f"  {name:<{max_name}}  {val_str:<{max_val}}  {expr}")


def print_hexdump(capture: dict, ecu_label: str, parameters: dict):
    """Print annotated hex dump showing which bytes each parameter uses."""
    payload = hex_to_bytes(capture["payload"])
    ecu_name = capture.get("ecu") or capture.get("ecu_name", "?")
    pid = capture.get("pid", "?")
    tx = capture.get("ecu_tx")
    tx_str = f"0x{tx:03X}" if isinstance(tx, int) else ""

    print(f"\n{'═' * 72}")
    print(f"  {ecu_name} ({ecu_label}) — PID {pid}" + (f" — TX {tx_str}" if tx_str else ""))
    print(f"{'═' * 72}")

    # Print hex dump with byte indices
    for row_start in range(0, len(payload), 16):
        row_end = min(row_start + 16, len(payload))
        hex_part = " ".join(f"{payload[j]:02X}" for j in range(row_start, row_end))
        idx_part = " ".join(f"{j:2d}" for j in range(row_start, row_end))
        print(f"  Idx:  {idx_part}")
        print(f"  Hex:  {hex_part}")
        print()

    # Map byte indices to parameter names
    byte_map: dict[int, list[str]] = {}
    for pname, pdef in parameters.items():
        expr = pdef.get("expression", "")
        # Extract byte references from expression
        for m in re.finditer(r'[BS](\d+)', expr):
            idx = int(m.group(1))
            byte_map.setdefault(idx, []).append(pname)
        for m in re.finditer(r'\[[BS](\d+):[BS](\d+)\]', expr):
            for idx in range(int(m.group(1)), int(m.group(2)) + 1):
                byte_map.setdefault(idx, []).append(pname)

    if byte_map:
        print("  Byte → Parameter mapping:")
        for idx in sorted(byte_map.keys()):
            if idx < len(payload):
                names = ", ".join(sorted(set(byte_map[idx])))
                print(f"    B{idx:02d} = 0x{payload[idx]:02X} ({payload[idx]:3d})  ← {names}")


# ─── Main ─────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="Decode captured UDS response payloads")
    parser.add_argument("--session", help="Filter by session date (e.g. 2025-08-04)")
    parser.add_argument("--ecu", help="Filter by ECU name (e.g. BMS, VCU)")
    parser.add_argument("--pid", help="Filter by PID (e.g. 2101)")
    parser.add_argument("--param", help="Filter by parameter name (e.g. SOC_BMS)")
    parser.add_argument("--hexdump", action="store_true", help="Show annotated hex dump per capture")
    parser.add_argument("--raw", help="Decode a raw hex payload directly (use with --expr or --pid)")
    parser.add_argument("--expr", help="Evaluate a single expression (use with --raw)")
    parser.add_argument("--pids-file", default=PIDS_FILE, help="Path to PID definitions YAML")
    parser.add_argument("--captures-file", default=None, help="Path to captures YAML file or directory (default: captures/)")

    args = parser.parse_args()

    # Direct expression evaluation mode
    if args.raw and args.expr:
        data = hex_to_bytes(args.raw)
        result = evaluate_expression(args.expr, data)
        result = round(result * 100) / 100
        print(f"{result}")
        return

    # Direct raw payload decode with PID definitions
    if args.raw and args.pid:
        pids_data = load_pids(args.pids_file)
        data = hex_to_bytes(args.raw)
        # Search all ECUs for this PID
        for ecu_label, ecu_def in pids_data.get("ecus", {}).items():
            for pid_key, pid_def in ecu_def.get("pids", {}).items():
                if str(pid_key).upper() == args.pid.upper():
                    params = pid_def.get("parameters", {})
                    results = decode_capture(data, params)
                    capture = {"ecu_name": ecu_label, "pid": args.pid,
                               "ecu_tx": ecu_def.get("tx_id", "?"), "notes": ""}
                    print_decoded(capture, ecu_label, params, results)
        return

    if args.raw:
        print("Error: --raw requires either --expr or --pid", file=sys.stderr)
        sys.exit(1)

    # Decode captures from file
    pids_data = load_pids(args.pids_file)
    captures_data = load_captures(args.captures_file)
    ecus_lookup = load_ecus_lookup()

    total_decoded = 0
    total_errors = 0
    total_no_def = 0

    for session in captures_data.get("sessions", []):
        session_date = session.get("date", "")
        session_label = session.get("label", "")

        if args.session and args.session not in session_date:
            continue

        first_in_session = True

        for capture in session.get("captures", []):
            # Support both old (ecu_name/ecu_tx) and new (ecu) field formats
            ecu_name = capture.get("ecu") or capture.get("ecu_name", "")
            pid = capture.get("pid", "")
            ecu_tx = capture.get("ecu_tx") or ecus_lookup.get(ecu_name.upper())

            # Apply filters
            if args.ecu and args.ecu.upper() != ecu_name.upper():
                continue
            if args.pid and args.pid.upper() != pid.upper():
                continue

            # Find matching PID definition
            ecu_label, pid_def, parameters = find_pid_definition(pids_data, ecu_tx, pid)

            if not parameters:
                total_no_def += 1
                if not args.param:  # Don't show "no definition" when filtering by param
                    tx_str = f"0x{ecu_tx:03X}" if isinstance(ecu_tx, int) else ""
                    suffix = f" (TX {tx_str})" if tx_str else ""
                    print(f"\n  {ecu_name} PID {pid}{suffix}: no PID definition found")
                continue

            # Filter by parameter name
            if args.param:
                filtered = {k: v for k, v in parameters.items()
                            if args.param.upper() in k.upper()}
                if not filtered:
                    continue
                parameters = filtered

            # Skip non-payload captures (experiments, scans)
            if "payload" not in capture:
                continue

            payload_bytes = hex_to_bytes(capture["payload"])

            if first_in_session:
                print(f"\n{'━' * 72}")
                print(f"  Session: {session_date} — {session_label}")
                if session.get("notes"):
                    print(f"  {session['notes']}")
                print(f"{'━' * 72}")
                first_in_session = False

            if args.hexdump:
                print_hexdump(capture, ecu_label, parameters)

            results = decode_capture(payload_bytes, parameters)
            print_decoded(capture, ecu_label, parameters, results)

            for _, value, _, _, error in results:
                if error:
                    total_errors += 1
                else:
                    total_decoded += 1

    print(f"\n{'─' * 72}")
    print(f"  Decoded: {total_decoded} values, {total_errors} errors, "
          f"{total_no_def} captures without PID definitions")
    print(f"{'─' * 72}")


if __name__ == "__main__":
    main()
