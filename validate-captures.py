#!/usr/bin/env python3
"""Validate capture files against captures/schema.json (JSON Schema).

Structural validation is delegated to the JSON Schema in captures/schema.json.
A few cross-file invariants that JSON Schema can't express are checked here:
  * ecu response address (non-sentinel) must exist in ecus.yaml (RX = TX + 8)
  * the session date must match the filename stem
  * deprecated fields get a clearer message than a bare "additional property"
"""

import json
import sys
from datetime import datetime
from pathlib import Path

import yaml
from jsonschema import Draft202012Validator

sys.path.insert(0, str(Path(__file__).parent))

CAPTURES_DIR = Path(__file__).parent / "captures"
SCHEMA_FILE = CAPTURES_DIR / "schema.json"

DEPRECATED_FIELDS = {"ecu_tx", "ecu_rx", "ecu_name", "decoded"}


def load_valid_rx_addrs() -> set[int]:
    """Set of valid ECU CAN response addresses (RX = request TX + 8)."""
    from canlib.ecus import build_rx_index

    return set(build_rx_index())


def _path_str(abs_path) -> str:
    """Render a jsonschema error path (deque) as e.g. sessions[0].captures[3].ecu."""
    out = ""
    for part in abs_path:
        if isinstance(part, int):
            out += f"[{part}]"
        else:
            out += f".{part}" if out else part
    return out or "<root>"


def validate_file(path: Path, validator: Draft202012Validator, rx_addrs: set[int]) -> list[str]:
    from canlib.ecus import SENTINELS, parse_ecu_ref

    errors: list[str] = []
    with open(path) as f:
        data = yaml.safe_load(f)

    # Schema validation (structure, types, required/allowed fields, patterns).
    for err in sorted(validator.iter_errors(data), key=lambda e: list(e.absolute_path)):
        loc = _path_str(err.absolute_path)
        # Nicer message for known-deprecated fields flagged by additionalProperties.
        dep = DEPRECATED_FIELDS & set(err.instance) if isinstance(err.instance, dict) else set()
        if err.validator == "additionalProperties" and dep:
            errors.append(f"{loc}: deprecated field(s): {sorted(dep)}")
        else:
            errors.append(f"{loc}: {err.message}")

    # Cross-file / cross-field checks not expressible in JSON Schema.
    if isinstance(data, dict):
        for si, session in enumerate(data.get("sessions", []) or []):
            if not isinstance(session, dict):
                continue
            date = session.get("date", "")
            if date:
                # Schema checks the shape; verify the leading date is a real
                # calendar date (a "-<suffix>" for same-day sessions is allowed).
                try:
                    datetime.strptime(str(date)[:10], "%Y-%m-%d")
                except ValueError:
                    errors.append(f"sessions[{si}]: date '{date}' is not a valid calendar date")
                if path.stem != date:
                    errors.append(f"sessions[{si}]: date '{date}' doesn't match filename '{path.stem}'")

            for ci, cap in enumerate(session.get("captures", []) or []):
                if not isinstance(cap, dict):
                    continue
                ecu = cap.get("ecu")
                if ecu and str(ecu).lower() not in SENTINELS:
                    rx = parse_ecu_ref(ecu)
                    if rx is not None and rx not in rx_addrs:
                        errors.append(
                            f"sessions[{si}].captures[{ci}].ecu: response address "
                            f"'{ecu}' not in ecus.yaml (RX = TX + 8)"
                        )

    return errors


def main():
    with open(SCHEMA_FILE) as f:
        schema = json.load(f)
    Draft202012Validator.check_schema(schema)
    validator = Draft202012Validator(schema)
    rx_addrs = load_valid_rx_addrs()

    files = sorted(CAPTURES_DIR.glob("*.yaml"))
    files = [f for f in files if f.name != "SCHEMA.yaml"]

    if not files:
        print("No capture files found.")
        return 1

    total_errors = 0
    for path in files:
        errors = validate_file(path, validator, rx_addrs)
        if errors:
            print(f"\n{path.name}: {len(errors)} errors")
            for e in errors:
                print(f"  - {e}")
            total_errors += len(errors)
        else:
            print(f"{path.name}: OK")

    if total_errors:
        print(f"\n{total_errors} total errors across {len(files)} files")
        return 1
    else:
        print(f"\nAll {len(files)} files valid.")
        return 0


if __name__ == "__main__":
    sys.exit(main())
