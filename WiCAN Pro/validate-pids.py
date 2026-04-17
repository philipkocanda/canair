#!/usr/bin/env python3
"""Validate per-ECU PID definition files in pids/ against _schema.yaml.

Usage:
    python3 validate-pids.py              # Validate all ECU files
    python3 validate-pids.py pids/bms.yaml  # Validate specific file(s)
    python3 validate-pids.py --stats      # Show parameter statistics
"""

import argparse
import re
import sys
from pathlib import Path

import yaml

SCRIPT_DIR = Path(__file__).parent
PIDS_DIR = SCRIPT_DIR / "pids"
SCHEMA_FILE = PIDS_DIR / "_schema.yaml"

REQUIRED_PARAM_FIELDS = {"expression", "unit"}
OPTIONAL_PARAM_FIELDS = {
    "ha_class",
    "mqtt_topic",
    "min",
    "max",
    "source",
    "source_links",
    "verified",
    "notes",
    "enabled",
    "display",
}
ALL_PARAM_FIELDS = REQUIRED_PARAM_FIELDS | OPTIONAL_PARAM_FIELDS
REQUIRED_ECU_FIELDS = {"tx_id", "pids"}
REQUIRED_META_FIELDS = {"car_model", "init"}

# Regex for valid WiCAN expressions (basic sanity — not a full parser)
EXPR_TOKEN_RE = re.compile(
    r"\[[BS]\d+:[BS]\d+\]|"  # [Bnn:Bmm] multi-byte (must be before Bnn:k)
    r"[BS]\d+:\d+|"  # Bnn:k (bit access)
    r"[BS]\d+|"  # Bnn, Snn
    r"V|"  # external value
    r"[0-9]+\.?[0-9]*|"  # numeric literal
    r"[+\-*/()&|^<>=\s]"  # operators, whitespace
)


def validate_expression(expr: str, param_name: str, pid: str, ecu: str) -> list[str]:
    """Basic expression syntax check."""
    errors = []
    if not expr or not expr.strip():
        errors.append(f"{ecu}/{pid}/{param_name}: empty expression")
        return errors

    # Check for completely unrecognizable tokens
    cleaned = EXPR_TOKEN_RE.sub("", expr)
    if cleaned.strip():
        errors.append(
            f"{ecu}/{pid}/{param_name}: suspicious expression chars: '{cleaned.strip()}' in '{expr}'"
        )
    return errors


def validate_ecu_file(path: Path) -> tuple[list[str], list[str], dict]:
    """Validate a single ECU YAML file.

    Returns (errors, warnings, stats).
    """
    errors = []
    warnings = []
    stats = {"ecus": 0, "pids": 0, "params": 0, "verified": 0, "unverified": 0}

    try:
        with open(path) as f:
            data = yaml.safe_load(f)
    except yaml.YAMLError as e:
        return [f"{path.name}: YAML parse error: {e}"], [], stats

    if not data or not isinstance(data, dict):
        return [f"{path.name}: empty or invalid YAML"], [], stats

    for ecu_name, ecu_def in data.items():
        stats["ecus"] += 1

        if not isinstance(ecu_def, dict):
            errors.append(f"{path.name}/{ecu_name}: ECU definition must be a dict")
            continue

        # Check required ECU fields
        for field in REQUIRED_ECU_FIELDS:
            if field not in ecu_def:
                errors.append(f"{path.name}/{ecu_name}: missing required field '{field}'")

        # Validate tx_id
        tx_id = ecu_def.get("tx_id")
        if tx_id is not None:
            if not isinstance(tx_id, int) or tx_id < 0 or tx_id > 0x7FF:
                errors.append(f"{path.name}/{ecu_name}: tx_id must be 0x000-0x7FF, got {tx_id}")

        # Validate PIDs
        pids = ecu_def.get("pids", {})
        if not isinstance(pids, dict):
            errors.append(f"{path.name}/{ecu_name}: 'pids' must be a dict")
            continue

        for pid_code, pid_def in pids.items():
            stats["pids"] += 1
            pid_str = str(pid_code)

            if not isinstance(pid_def, dict):
                errors.append(f"{path.name}/{ecu_name}/{pid_str}: PID definition must be a dict")
                continue

            # Validate period
            period = pid_def.get("period")
            if period is not None and (not isinstance(period, int) or period < 0):
                errors.append(f"{path.name}/{ecu_name}/{pid_str}: period must be positive int")

            # Validate parameters
            params = pid_def.get("parameters", {})
            if not isinstance(params, dict):
                errors.append(f"{path.name}/{ecu_name}/{pid_str}: 'parameters' must be a dict")
                continue

            if not params:
                warnings.append(f"{path.name}/{ecu_name}/{pid_str}: no parameters defined")

            for param_name, param in params.items():
                stats["params"] += 1

                if not isinstance(param, dict):
                    errors.append(f"{ecu_name}/{pid_str}/{param_name}: parameter must be a dict")
                    continue

                # Required fields
                for field in REQUIRED_PARAM_FIELDS:
                    if field not in param:
                        errors.append(f"{ecu_name}/{pid_str}/{param_name}: missing '{field}'")

                # Unknown fields
                for field in param:
                    if field not in ALL_PARAM_FIELDS:
                        warnings.append(
                            f"{ecu_name}/{pid_str}/{param_name}: unknown field '{field}'"
                        )

                # Expression validation
                expr = param.get("expression", "")
                if expr:
                    errors.extend(validate_expression(expr, param_name, pid_str, ecu_name))

                # Stats
                if param.get("verified", False):
                    stats["verified"] += 1
                else:
                    stats["unverified"] += 1

    return errors, warnings, stats


def validate_meta(path: Path) -> list[str]:
    """Validate _meta.yaml."""
    errors = []
    try:
        with open(path) as f:
            data = yaml.safe_load(f)
    except yaml.YAMLError as e:
        return [f"_meta.yaml: YAML parse error: {e}"]

    if not data or not isinstance(data, dict):
        return ["_meta.yaml: empty or invalid"]

    for field in REQUIRED_META_FIELDS:
        if field not in data:
            errors.append(f"_meta.yaml: missing required field '{field}'")

    return errors


def main():
    parser = argparse.ArgumentParser(description="Validate PID definition files")
    parser.add_argument("files", nargs="*", help="Specific files to validate (default: all)")
    parser.add_argument("--stats", action="store_true", help="Show parameter statistics")
    args = parser.parse_args()

    all_errors = []
    all_warnings = []
    total_stats = {"ecus": 0, "pids": 0, "params": 0, "verified": 0, "unverified": 0}

    if args.files:
        files = [Path(f) for f in args.files]
    else:
        files = sorted(PIDS_DIR.glob("*.yaml"))

    for fpath in files:
        if fpath.name == "_schema.yaml":
            continue

        if fpath.name == "_meta.yaml":
            all_errors.extend(validate_meta(fpath))
            continue

        errors, warnings, stats = validate_ecu_file(fpath)
        all_errors.extend(errors)
        all_warnings.extend(warnings)
        for k in total_stats:
            total_stats[k] += stats[k]

    # Print results
    if all_warnings:
        for w in all_warnings:
            print(f"  WARN: {w}")
        print()

    if all_errors:
        for e in all_errors:
            print(f"  ERROR: {e}")
        print(f"\n{len(all_errors)} error(s), {len(all_warnings)} warning(s)")
        sys.exit(1)

    n_files = len([f for f in files if f.name not in ("_schema.yaml", "_meta.yaml")])
    print(
        f"OK — {n_files} ECU files, {total_stats['pids']} PIDs, "
        f"{total_stats['params']} parameters "
        f"({total_stats['verified']} verified, {total_stats['unverified']} unverified)"
    )

    if all_warnings:
        print(f"  {len(all_warnings)} warning(s)")

    if args.stats:
        print(f"\n  ECUs:       {total_stats['ecus']}")
        print(f"  PIDs:       {total_stats['pids']}")
        print(f"  Parameters: {total_stats['params']}")
        print(f"  Verified:   {total_stats['verified']}")
        print(f"  Unverified: {total_stats['unverified']}")


if __name__ == "__main__":
    main()
