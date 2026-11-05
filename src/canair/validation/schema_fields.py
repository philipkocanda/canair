"""The ECU schema (``pids_schema.yaml``) and the field vocabularies the ECU validators read."""

import sys
from dataclasses import dataclass
from pathlib import Path

from canair.core import yaml_io
from canair.ecus.pids import PID_STATUSES

from ._common import SCHEMA_FILE


def load_schema(path: Path = SCHEMA_FILE) -> dict:
    """Load schema definition from _schema.yaml."""
    with open(path) as f:
        schema = yaml_io.safe_load(f)
    if not schema or not isinstance(schema, dict):
        print(f"ERROR: {path} is empty or invalid", file=sys.stderr)
        sys.exit(1)
    return schema


@dataclass(frozen=True)
class SchemaFields:
    """Field-name sets parsed once from the schema, threaded through validation.

    Bundles the ~dozen ``set(...)`` field vocabularies the per-ECU validators
    consult so they can take one ``fields`` argument instead of a long list.
    """

    required_ecu: set
    all_ecu: set
    required_pid: set
    all_pid: set
    required_param: set
    all_param: set
    identity: set
    valid_protocols: set
    valid_confidence: set
    scan_log: set
    dtcs: set
    sessions: set
    valid_pid_status: set
    valid_param_types: set
    wake: set
    valid_wake_methods: set

    @classmethod
    def from_schema(cls, schema: dict) -> "SchemaFields":
        required_ecu = set(schema.get("required_ecu_fields", []))
        optional_ecu = set(schema.get("optional_ecu_fields", []))
        required_pid = set(schema.get("required_pid_fields", []))
        optional_pid = set(schema.get("optional_pid_fields", []))
        required_param = set(schema.get("required_param_fields", []))
        identity = schema.get("identity_fields", {}) or {}
        wake_fields = schema.get("wake_fields", {}) or {}
        return cls(
            required_ecu=required_ecu,
            all_ecu=required_ecu | optional_ecu,
            required_pid=required_pid,
            all_pid=required_pid | optional_pid,
            required_param=required_param,
            all_param=required_param | set(schema.get("optional_param_fields", [])),
            identity=set(identity.get("optional", [])) | set(identity.get("required", [])),
            valid_protocols=set(schema.get("valid_id_protocols", [])),
            valid_confidence=set(schema.get("valid_identity_confidence", [])),
            scan_log=set((schema.get("scan_log_entry_fields", {}) or {}).get("optional", [])),
            dtcs=set((schema.get("dtcs_fields", {}) or {}).get("optional", [])),
            sessions=set((schema.get("sessions_fields", {}) or {}).get("optional", [])),
            # Fallback derives from the Python vocabulary (canair.ecus.pids.PidStatus)
            # rather than repeating the values — a third copy would be a third
            # thing to drift. tests/test_pids.py guards schema ↔ Literal agreement.
            valid_pid_status=set(schema.get("valid_pid_status", [])) or set(PID_STATUSES),
            valid_param_types=set(schema.get("valid_param_types", []))
            or {"numeric", "enum", "bitmask", "ascii", "date", "bcd", "struct"},
            wake=set(wake_fields.get("optional", [])) | set(wake_fields.get("required", [])),
            valid_wake_methods=set(schema.get("valid_wake_methods", []))
            or {"rapid_read", "session", "relay"},
        )


def new_ecu_stats() -> dict:
    """Fresh zeroed stats accumulator for one ECU file."""
    return {
        "ecus": 0,
        "pids": 0,
        "params": 0,
        "verified": 0,
        "unverified": 0,
        "ignored": 0,
        "iocontrol": 0,
        "research": 0,
        "routines": 0,
        "iocontrol_discoveries": 0,
        "scan_log": 0,
        "dtcs": 0,
        "sessions": 0,
    }
