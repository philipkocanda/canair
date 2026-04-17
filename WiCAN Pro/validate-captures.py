#!/usr/bin/env python3
"""Validate capture files against the schema defined in captures/SCHEMA.yaml."""

import sys
from pathlib import Path

import yaml

CAPTURES_DIR = Path(__file__).parent / "captures"
ECUS_FILE = Path(__file__).parent / "ecus.yaml"

PAYLOAD_FIELDS = {"payload", "response", "scan_results"}
REQUIRED_CAPTURE_FIELDS = {"ecu", "pid", "notes"}
ALLOWED_CAPTURE_FIELDS = REQUIRED_CAPTURE_FIELDS | PAYLOAD_FIELDS | {"label"}
DEPRECATED_FIELDS = {"ecu_tx", "ecu_rx", "ecu_name"}
REQUIRED_SESSION_FIELDS = {"date", "label", "captures"}
ALLOWED_SESSION_FIELDS = REQUIRED_SESSION_FIELDS | {"state", "notes"}


def load_ecu_names() -> set[str]:
    with open(ECUS_FILE) as f:
        data = yaml.safe_load(f)
    return {info["name"] for info in data.get("ecus", {}).values()}


def validate_file(path: Path, ecu_names: set[str]) -> list[str]:
    errors = []
    with open(path) as f:
        data = yaml.safe_load(f)

    if not isinstance(data, dict) or "sessions" not in data:
        errors.append("Missing root 'sessions' key")
        return errors

    for si, session in enumerate(data["sessions"]):
        prefix = f"session[{si}]"

        # Session-level checks
        for field in REQUIRED_SESSION_FIELDS:
            if field not in session:
                errors.append(f"{prefix}: missing required field '{field}'")

        for field in session:
            if field not in ALLOWED_SESSION_FIELDS:
                errors.append(f"{prefix}: unknown field '{field}'")

        date = session.get("date", "")
        if date and not (len(date) == 10 and date[4] == "-" and date[7] == "-"):
            errors.append(f"{prefix}: date '{date}' not in YYYY-MM-DD format")

        # Check filename matches date
        expected_stem = date
        if expected_stem and path.stem != expected_stem:
            errors.append(f"{prefix}: date '{date}' doesn't match filename '{path.stem}'")

        captures = session.get("captures", [])
        if not isinstance(captures, list):
            errors.append(f"{prefix}: 'captures' must be a list")
            continue

        for ci, cap in enumerate(captures):
            cprefix = f"{prefix}.captures[{ci}]"

            if not isinstance(cap, dict):
                errors.append(f"{cprefix}: capture must be a mapping")
                continue

            # Check for deprecated fields
            deprecated_found = DEPRECATED_FIELDS & set(cap)
            if deprecated_found:
                errors.append(f"{cprefix}: deprecated fields: {deprecated_found}")

            # Check required fields
            for field in REQUIRED_CAPTURE_FIELDS:
                if field not in cap:
                    errors.append(f"{cprefix}: missing required field '{field}'")

            # Check unknown fields
            unknown = set(cap) - ALLOWED_CAPTURE_FIELDS - DEPRECATED_FIELDS
            if unknown:
                errors.append(f"{cprefix}: unknown fields: {unknown}")

            # Exactly one payload field
            present_payload = PAYLOAD_FIELDS & set(cap)
            if len(present_payload) == 0:
                errors.append(
                    f"{cprefix}: missing payload (need one of: payload, response, scan_results)"
                )
            elif len(present_payload) > 1:
                errors.append(f"{cprefix}: multiple payload fields: {present_payload}")

            # Validate ECU name
            ecu = cap.get("ecu")
            if ecu and ecu not in ecu_names:
                errors.append(f"{cprefix}: unknown ECU '{ecu}' (not in ecus.yaml)")

            # Validate scan_results structure
            if "scan_results" in cap:
                sr = cap["scan_results"]
                if not isinstance(sr, dict):
                    errors.append(f"{cprefix}: scan_results must be a mapping")
                elif "responding" in sr:
                    for ri, entry in enumerate(sr["responding"]):
                        if not isinstance(entry, dict):
                            errors.append(
                                f"{cprefix}.scan_results.responding[{ri}]: must be a mapping"
                            )
                        elif "did" not in entry or "response" not in entry:
                            errors.append(
                                f"{cprefix}.scan_results.responding[{ri}]: needs 'did' and 'response'"
                            )

    return errors


def main():
    ecu_names = load_ecu_names()
    files = sorted(CAPTURES_DIR.glob("*.yaml"))
    files = [f for f in files if f.name != "SCHEMA.yaml"]

    if not files:
        print("No capture files found.")
        return 1

    total_errors = 0
    for path in files:
        errors = validate_file(path, ecu_names)
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
