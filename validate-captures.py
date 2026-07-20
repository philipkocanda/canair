#!/usr/bin/env python3
"""Validate capture files against the schema defined in captures/SCHEMA.yaml."""

import sys
from datetime import datetime
from pathlib import Path

import yaml

sys.path.insert(0, str(Path(__file__).parent))

CAPTURES_DIR = Path(__file__).parent / "captures"
ECUS_FILE = Path(__file__).parent / "ecus.yaml"

PAYLOAD_FIELDS = {"payload", "response", "scan_results"}
REQUIRED_CAPTURE_FIELDS = {"ecu", "pid"}
ALLOWED_CAPTURE_FIELDS = (
    REQUIRED_CAPTURE_FIELDS | PAYLOAD_FIELDS | {"label", "time", "notes"}
)
DEPRECATED_FIELDS = {"ecu_tx", "ecu_rx", "ecu_name", "decoded"}
REQUIRED_SESSION_FIELDS = {"date", "label", "captures"}
ALLOWED_SESSION_FIELDS = REQUIRED_SESSION_FIELDS | {"state", "notes"}


def _valid_session_date(date: str) -> bool:
    """True if ``date`` is YYYY-MM-DD, optionally with a "-<suffix>" (same-day)."""
    try:
        datetime.strptime(date[:10], "%Y-%m-%d")
    except ValueError:
        return False
    # Bare date, or a "-<suffix>" after the 10-char date.
    return len(date) == 10 or (len(date) > 11 and date[10] == "-")


def load_valid_rx_addrs() -> set[int]:
    """Set of valid ECU CAN response addresses (RX = request TX + 8)."""
    from canlib.ecus import build_rx_index

    return set(build_rx_index())


def validate_file(path: Path, rx_addrs: set[int]) -> list[str]:
    from canlib.ecus import SENTINELS, parse_ecu_ref

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
        # A date is YYYY-MM-DD, optionally with a "-<suffix>" for a second
        # session captured on the same day (e.g. "2026-04-17-b").
        if date and not _valid_session_date(date):
            errors.append(f"{prefix}: date '{date}' not in YYYY-MM-DD[-suffix] format")

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

            # Validate ECU response address (or a sentinel like "broadcast")
            ecu = cap.get("ecu")
            if ecu and str(ecu).lower() not in SENTINELS:
                rx = parse_ecu_ref(ecu)
                if rx is None:
                    errors.append(f"{cprefix}: ECU '{ecu}' is not a valid response address or sentinel")
                elif rx not in rx_addrs:
                    errors.append(f"{cprefix}: ECU response address '{ecu}' not in ecus.yaml (RX = TX + 8)")

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
                        elif ("did" not in entry and "ecu" not in entry) or "response" not in entry:
                            errors.append(
                                f"{cprefix}.scan_results.responding[{ri}]: needs 'did'/'ecu' and 'response'"
                            )

    return errors


def main():
    rx_addrs = load_valid_rx_addrs()
    files = sorted(CAPTURES_DIR.glob("*.yaml"))
    files = [f for f in files if f.name != "SCHEMA.yaml"]

    if not files:
        print("No capture files found.")
        return 1

    total_errors = 0
    for path in files:
        errors = validate_file(path, rx_addrs)
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
