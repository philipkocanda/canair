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

# Regex for valid WiCAN expressions (basic sanity — not a full parser)
EXPR_TOKEN_RE = re.compile(
    r"\[[BS]\d+:[BS]\d+\]|"  # [Bnn:Bmm] multi-byte (must be before Bnn:k)
    r"[BS]\d+:\d+|"  # Bnn:k (bit access)
    r"[BS]\d+|"  # Bnn, Snn
    r"V|"  # external value
    r"[0-9]+\.?[0-9]*|"  # numeric literal
    r"[+\-*/()&|^<>=\s]"  # operators, whitespace
)


def load_schema(path: Path = SCHEMA_FILE) -> dict:
    """Load schema definition from _schema.yaml."""
    with open(path) as f:
        schema = yaml.safe_load(f)
    if not schema or not isinstance(schema, dict):
        print(f"ERROR: {path} is empty or invalid", file=sys.stderr)
        sys.exit(1)
    return schema


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


def validate_ecu_file(
    path: Path,
    required_ecu_fields: set,
    optional_ecu_fields: set,
    required_pid_fields: set,
    optional_pid_fields: set,
    required_param_fields: set,
    all_param_fields: set,
) -> tuple[list[str], list[str], dict]:
    """Validate a single ECU YAML file.

    Returns (errors, warnings, stats).
    """
    errors = []
    warnings = []
    stats = {
        "ecus": 0,
        "pids": 0,
        "params": 0,
        "verified": 0,
        "unverified": 0,
        "ignored": 0,
        "iocontrol": 0,
        "research": 0,
    }

    try:
        with open(path) as f:
            data = yaml.safe_load(f)
    except yaml.YAMLError as e:
        return [f"{path.name}: YAML parse error: {e}"], [], stats

    if not data or not isinstance(data, dict):
        return [f"{path.name}: empty or invalid YAML"], [], stats

    all_ecu_fields = required_ecu_fields | optional_ecu_fields | {"iocontrol"}
    all_pid_fields = required_pid_fields | optional_pid_fields

    for ecu_name, ecu_def in data.items():
        stats["ecus"] += 1

        if not isinstance(ecu_def, dict):
            errors.append(f"{path.name}/{ecu_name}: ECU definition must be a dict")
            continue

        # Check required ECU fields
        for field in required_ecu_fields:
            if field not in ecu_def:
                errors.append(f"{path.name}/{ecu_name}: missing required field '{field}'")

        # Check unknown ECU fields
        for field in ecu_def:
            if field not in all_ecu_fields:
                warnings.append(f"{path.name}/{ecu_name}: unknown ECU field '{field}'")

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

            # Check required PID fields
            for field in required_pid_fields:
                if field not in pid_def:
                    errors.append(
                        f"{path.name}/{ecu_name}/{pid_str}: missing required PID field '{field}'"
                    )

            # Check unknown PID fields
            for field in pid_def:
                if field not in all_pid_fields:
                    warnings.append(
                        f"{path.name}/{ecu_name}/{pid_str}: unknown PID field '{field}'"
                    )

            # Validate period
            period = pid_def.get("period")
            if period is not None and (not isinstance(period, int) or period < 0):
                errors.append(f"{path.name}/{ecu_name}/{pid_str}: period must be positive int")

            # Validate availability
            VALID_AVAILABILITY = {"sleep", "acc", "ign", "ready", "charging"}
            avail = pid_def.get("availability")
            if avail is not None:
                if not isinstance(avail, list):
                    errors.append(
                        f"{path.name}/{ecu_name}/{pid_str}: availability must be a list"
                    )
                else:
                    for v in avail:
                        if v not in VALID_AVAILABILITY:
                            errors.append(
                                f"{path.name}/{ecu_name}/{pid_str}: invalid availability "
                                f"value '{v}' (allowed: {sorted(VALID_AVAILABILITY)})"
                            )
                    if len(avail) != len(set(avail)):
                        errors.append(
                            f"{path.name}/{ecu_name}/{pid_str}: duplicate availability values"
                        )

            # Ignored PIDs — count and skip parameter validation
            if pid_def.get("ignored", False):
                stats["ignored"] += 1
                continue

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
                for field in required_param_fields:
                    if field not in param:
                        errors.append(f"{ecu_name}/{pid_str}/{param_name}: missing '{field}'")

                # Unknown fields
                for field in param:
                    if field not in all_param_fields:
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

        # Validate IOControl section
        iocontrol = ecu_def.get("iocontrol")
        if iocontrol is not None:
            if not isinstance(iocontrol, dict):
                errors.append(f"{path.name}/{ecu_name}: 'iocontrol' must be a dict")
            else:
                valid_fields = {
                    "label",
                    "verified",
                    "on",
                    "off",
                    "notes",
                    "session",
                    "hold",
                    True,
                    False,  # YAML bool keys from unquoted on/off
                }
                for did, did_def in iocontrol.items():
                    did_str = str(did)
                    if not isinstance(did_def, dict):
                        errors.append(f"{path.name}/{ecu_name}/iocontrol/{did_str}: must be a dict")
                        continue
                    if "label" not in did_def:
                        warnings.append(
                            f"{path.name}/{ecu_name}/iocontrol/{did_str}: missing 'label'"
                        )
                    for field in did_def:
                        if field not in valid_fields:
                            warnings.append(
                                f"{path.name}/{ecu_name}/iocontrol/{did_str}: unknown field '{field}'"
                            )
                    stats["iocontrol"] += 1

        # Validate research section
        research = ecu_def.get("research")
        if research is not None:
            if not isinstance(research, list):
                errors.append(f"{path.name}/{ecu_name}: 'research' must be a list")
            else:
                valid_types = {"scan", "decode", "verify", "iocontrol_scan"}
                valid_statuses = {"pending", "captured", "nrc", "done"}
                valid_priorities = {"P1", "P2", "P3"}
                valid_prereqs = {"sleep", "acc", "ign", "ready", "charging"}
                research_optional = {
                    "priority",
                    "prerequisite",
                    "date",
                    "result",
                    "notes",
                    "sources",
                    "what_to_test",
                }
                research_required = {"type", "target", "status"}
                all_research_fields = research_required | research_optional

                for i, entry in enumerate(research):
                    label = f"{path.name}/{ecu_name}/research[{i}]"
                    if not isinstance(entry, dict):
                        errors.append(f"{label}: entry must be a dict")
                        continue

                    # Required fields
                    for field in research_required:
                        if field not in entry:
                            errors.append(f"{label}: missing required field '{field}'")

                    # Unknown fields
                    for field in entry:
                        if field not in all_research_fields:
                            warnings.append(f"{label}: unknown field '{field}'")

                    # Validate type
                    rtype = entry.get("type")
                    if rtype and rtype not in valid_types:
                        errors.append(
                            f"{label}: invalid type '{rtype}' (allowed: {sorted(valid_types)})"
                        )

                    # Validate status
                    rstatus = entry.get("status")
                    if rstatus and rstatus not in valid_statuses:
                        errors.append(
                            f"{label}: invalid status '{rstatus}' "
                            f"(allowed: {sorted(valid_statuses)})"
                        )

                    # Validate priority
                    rprio = entry.get("priority")
                    if rprio and rprio not in valid_priorities:
                        errors.append(
                            f"{label}: invalid priority '{rprio}' "
                            f"(allowed: {sorted(valid_priorities)})"
                        )

                    # Validate prerequisite
                    prereq = entry.get("prerequisite")
                    if prereq is not None:
                        if not isinstance(prereq, list):
                            errors.append(f"{label}: prerequisite must be a list")
                        else:
                            for v in prereq:
                                if v not in valid_prereqs:
                                    errors.append(
                                        f"{label}: invalid prerequisite '{v}' "
                                        f"(allowed: {sorted(valid_prereqs)})"
                                    )

                    stats["research"] += 1

    return errors, warnings, stats


def validate_meta(path: Path, required_fields: set) -> list[str]:
    """Validate _meta.yaml."""
    errors = []
    try:
        with open(path) as f:
            data = yaml.safe_load(f)
    except yaml.YAMLError as e:
        return [f"_meta.yaml: YAML parse error: {e}"]

    if not data or not isinstance(data, dict):
        return ["_meta.yaml: empty or invalid"]

    for field in required_fields:
        if field not in data:
            errors.append(f"_meta.yaml: missing required field '{field}'")

    return errors


def main():
    parser = argparse.ArgumentParser(description="Validate PID definition files")
    parser.add_argument("files", nargs="*", help="Specific files to validate (default: all)")
    parser.add_argument("--stats", action="store_true", help="Show parameter statistics")
    args = parser.parse_args()

    # Load schema
    schema = load_schema()
    required_ecu_fields = set(schema.get("required_ecu_fields", []))
    optional_ecu_fields = set(schema.get("optional_ecu_fields", []))
    required_pid_fields = set(schema.get("required_pid_fields", []))
    optional_pid_fields = set(schema.get("optional_pid_fields", []))
    required_param_fields = set(schema.get("required_param_fields", []))
    optional_param_fields = set(schema.get("optional_param_fields", []))
    all_param_fields = required_param_fields | optional_param_fields
    required_meta_fields = set(schema.get("required_meta_fields", []))

    all_errors = []
    all_warnings = []
    total_stats = {
        "ecus": 0,
        "pids": 0,
        "params": 0,
        "verified": 0,
        "unverified": 0,
        "ignored": 0,
        "iocontrol": 0,
        "research": 0,
    }

    if args.files:
        files = [Path(f) for f in args.files]
    else:
        files = sorted(PIDS_DIR.glob("*.yaml"))

    for fpath in files:
        if fpath.name == "_schema.yaml":
            continue

        if fpath.name == "_meta.yaml":
            all_errors.extend(validate_meta(fpath, required_meta_fields))
            continue

        errors, warnings, stats = validate_ecu_file(
            fpath,
            required_ecu_fields,
            optional_ecu_fields,
            required_pid_fields,
            optional_pid_fields,
            required_param_fields,
            all_param_fields,
        )
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
    ignored = total_stats["ignored"]
    ignored_str = f", {ignored} ignored" if ignored else ""
    ioctl = total_stats["iocontrol"]
    ioctl_str = f", {ioctl} IOControl DIDs" if ioctl else ""
    research = total_stats["research"]
    research_str = f", {research} research items" if research else ""
    print(
        f"OK — {n_files} ECU files, {total_stats['pids']} PIDs{ignored_str}{ioctl_str}{research_str}, "
        f"{total_stats['params']} parameters "
        f"({total_stats['verified']} verified, {total_stats['unverified']} unverified)"
    )

    if all_warnings:
        print(f"  {len(all_warnings)} warning(s)")

    if args.stats:
        print(f"\n  ECUs:       {total_stats['ecus']}")
        print(f"  PIDs:       {total_stats['pids']}")
        print(f"  Ignored:    {total_stats['ignored']}")
        print(f"  Parameters: {total_stats['params']}")
        print(f"  Verified:   {total_stats['verified']}")
        print(f"  Unverified: {total_stats['unverified']}")
        print(f"  IOControl:  {total_stats['iocontrol']}")
        print(f"  Research:   {total_stats['research']}")


if __name__ == "__main__":
    main()
