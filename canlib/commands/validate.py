"""Validate a profile's ecus/, profile.yaml, and captures/ against their schemas.

Validators merged into one subcommand:

  * ``validate pids`` — per-ECU definition files in ecus/ vs pids_schema.yaml
    (also validates profile.yaml). ``validate ecus`` is an alias.
  * ``validate captures`` — capture files in captures/ vs captures_schema.json
  * ``validate all`` (default) — run all of them

Usage:
    canair validate                 # validate ecus + captures
    canair validate pids            # validate all ECU files
    canair validate pids ecus/bms.yaml  # validate specific file(s)
    canair validate pids --stats    # show parameter statistics
    canair validate captures        # validate all capture files
"""

import argparse
import json
import re
import sys
from datetime import datetime
from pathlib import Path

import yaml
from jsonschema import Draft202012Validator
from jsonschema.protocols import Validator

from canlib.byteindex import wican_to_isotp
from canlib.constants import SCHEMA_DIR

SCHEMA_FILE = SCHEMA_DIR / "pids_schema.yaml"
CAPTURES_SCHEMA_FILE = SCHEMA_DIR / "captures_schema.json"

NAME = "validate"

# Regex for valid WiCAN expressions (basic sanity — not a full parser)
EXPR_TOKEN_RE = re.compile(
    r"\[[BS]\d+:[BS]\d+\]|"  # [Bnn:Bmm] multi-byte (must be before Bnn:k)
    r"[BS]\d+:\d+|"  # Bnn:k (bit access)
    r"[BS]\d+|"  # Bnn, Snn
    r"V|"  # external value
    r"[0-9]+\.?[0-9]*|"  # numeric literal
    r"[+\-*/()&|^<>=\s]"  # operators, whitespace
)

DEPRECATED_FIELDS = {"ecu_tx", "ecu_rx", "ecu_name", "decoded"}


# ── pids validation (from validate-pids.py) ───────────────────────────────


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


def check_pci_bytes(expr: str, param_name: str, pid: str, ecu: str) -> list[str]:
    """Warn if an expression reads ISO-TP PCI bytes.

    PCI (frame-header) bytes live at WiCAN indices 0, 1, 8, 16, 24, 32, ...
    (``wican_to_isotp`` returns None for them). Reading one yields the frame
    counter/length, not real data — a common byte-index mistake. This also
    flags multi-byte ranges ``[Bnn:Bmm]`` that *span* a PCI byte, since those
    read consecutive raw bytes without skipping PCI.
    """
    warnings = []

    def is_pci(idx: int) -> bool:
        return wican_to_isotp(idx) is None

    # Multi-byte ranges: [Bnn:Bmm] / [Snn:Smm] — flag if any index in range is PCI
    ranges = []
    for m in re.finditer(r"\[[BS](\d+):[BS](\d+)\]", expr):
        a, b = int(m.group(1)), int(m.group(2))
        lo, hi = min(a, b), max(a, b)
        ranges.append((m.span(), lo, hi))
        pci = [x for x in range(lo, hi + 1) if is_pci(x)]
        if pci:
            warnings.append(
                f"{ecu}/{pid}/{param_name}: range '{m.group(0)}' spans ISO-TP PCI byte(s) "
                f"{', '.join(f'B{x}' for x in pci)} — reads frame header, not data"
            )

    # Single byte refs: Bnn, Snn, Bnn:k — skip those inside a flagged range
    for m in re.finditer(r"[BS](\d+)", expr):
        idx = int(m.group(1))
        if any(start <= m.start() < end for (start, end), _, _ in ranges):
            continue
        if is_pci(idx):
            warnings.append(
                f"{ecu}/{pid}/{param_name}: reads ISO-TP PCI byte B{idx} "
                f"(frame header, not data) in '{expr}'"
            )
    return warnings


# Legacy PID fields removed in the status/vehicle_states consolidation. Presence
# of any of these is a hard error (migrate with scripts/migrate_states_status.py).
_LEGACY_PID_FIELDS = {
    "enabled": "use status: draft (PID-level) — enabled is param-level only now",
    "ignored": "use status: ignored",
    "static": "use status: static",
    "availability": "renamed to vehicle_states",
}


def _validate_state_list(value, label: str, field: str, errors: list, allowed: set) -> None:
    """Validate a ``vehicle_states``-style token list against ``allowed``."""
    if value is None:
        return
    if not isinstance(value, list):
        errors.append(f"{label}: {field} must be a list")
        return
    for v in value:
        if v not in allowed:
            errors.append(f"{label}: invalid {field} value '{v}' (allowed: {sorted(allowed)})")
    if len(value) != len(set(value)):
        errors.append(f"{label}: duplicate {field} values")


def validate_ecu_file(
    path: Path,
    schema: dict,
) -> tuple[list[str], list[str], dict]:
    """Validate a single ECU YAML file.

    Returns (errors, warnings, stats).
    """
    from canlib.states import allowed_states

    allowed_states_set = allowed_states()
    required_ecu_fields = set(schema.get("required_ecu_fields", []))
    optional_ecu_fields = set(schema.get("optional_ecu_fields", []))
    required_pid_fields = set(schema.get("required_pid_fields", []))
    optional_pid_fields = set(schema.get("optional_pid_fields", []))
    required_param_fields = set(schema.get("required_param_fields", []))
    all_param_fields = required_param_fields | set(schema.get("optional_param_fields", []))
    identity_fields = set((schema.get("identity_fields", {}) or {}).get("optional", [])) | set(
        (schema.get("identity_fields", {}) or {}).get("required", [])
    )
    valid_protocols = set(schema.get("valid_id_protocols", []))
    valid_confidence = set(schema.get("valid_identity_confidence", []))
    scan_log_fields = set((schema.get("scan_log_entry_fields", {}) or {}).get("optional", []))
    dtcs_fields = set((schema.get("dtcs_fields", {}) or {}).get("optional", []))
    sessions_fields = set((schema.get("sessions_fields", {}) or {}).get("optional", []))
    valid_pid_status = set(schema.get("valid_pid_status", [])) or {
        "active",
        "draft",
        "static",
        "ignored",
    }

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
        "routines": 0,
        "iocontrol_discoveries": 0,
        "scan_log": 0,
        "dtcs": 0,
        "sessions": 0,
    }

    try:
        with open(path) as f:
            data = yaml.safe_load(f)
    except yaml.YAMLError as e:
        return [f"{path.name}: YAML parse error: {e}"], [], stats

    if not data or not isinstance(data, dict):
        return [f"{path.name}: empty or invalid YAML"], [], stats

    all_ecu_fields = required_ecu_fields | optional_ecu_fields
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

        # Legacy ECU-level availability -> vehicle_states
        if "availability" in ecu_def:
            errors.append(
                f"{path.name}/{ecu_name}: legacy field 'availability' — renamed to "
                "vehicle_states (run scripts/migrate_states_status.py)"
            )
        _validate_state_list(
            ecu_def.get("vehicle_states"),
            f"{path.name}/{ecu_name}",
            "vehicle_states",
            errors,
            allowed_states_set,
        )

        # Validate tx_id
        tx_id = ecu_def.get("tx_id")
        if tx_id is not None:
            if not isinstance(tx_id, int) or tx_id < 0 or tx_id > 0x7FF:
                errors.append(f"{path.name}/{ecu_name}: tx_id must be 0x000-0x7FF, got {tx_id}")

        # Validate identity block
        _validate_identity(
            ecu_def,
            path,
            ecu_name,
            identity_fields,
            valid_protocols,
            valid_confidence,
            errors,
            warnings,
        )

        # Validate scan_log
        _validate_scan_log(ecu_def, path, ecu_name, scan_log_fields, errors, warnings, stats)

        # Validate dtcs
        _validate_dtcs(ecu_def, path, ecu_name, dtcs_fields, errors, warnings, stats)

        # Validate sessions (diagnostic session types — service 0x10)
        _validate_sessions(ecu_def, path, ecu_name, sessions_fields, errors, warnings, stats)

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

            # Reject legacy visibility booleans (migrated to `status:`) and the
            # renamed `availability:` — hard cut-over so no file straddles both.
            for legacy, hint in _LEGACY_PID_FIELDS.items():
                if legacy in pid_def:
                    errors.append(
                        f"{path.name}/{ecu_name}/{pid_str}: legacy field '{legacy}' — {hint} "
                        "(run scripts/migrate_states_status.py)"
                    )

            # Validate period
            period = pid_def.get("period")
            if period is not None and (not isinstance(period, int) or period < 0):
                errors.append(f"{path.name}/{ecu_name}/{pid_str}: period must be positive int")

            # Validate status (PID lifecycle)
            status = pid_def.get("status")
            if status is not None and status not in valid_pid_status:
                errors.append(
                    f"{path.name}/{ecu_name}/{pid_str}: invalid status '{status}' "
                    f"(allowed: {sorted(valid_pid_status)})"
                )

            # Validate vehicle_states
            _validate_state_list(
                pid_def.get("vehicle_states"),
                f"{path.name}/{ecu_name}/{pid_str}",
                "vehicle_states",
                errors,
                allowed_states_set,
            )

            # Ignored PIDs — count and skip parameter validation
            if str(pid_def.get("status", "active")).lower() == "ignored":
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
                    warnings.extend(check_pci_bytes(expr, param_name, pid_str, ecu_name))

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
                    "vehicle_states",
                    "status_param",
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
                    if "availability" in did_def:
                        errors.append(
                            f"{path.name}/{ecu_name}/iocontrol/{did_str}: legacy field "
                            "'availability' — renamed to vehicle_states "
                            "(run scripts/migrate_states_status.py)"
                        )
                    for field in did_def:
                        if field not in valid_fields:
                            warnings.append(
                                f"{path.name}/{ecu_name}/iocontrol/{did_str}: unknown field '{field}'"
                            )
                    _validate_state_list(
                        did_def.get("vehicle_states"),
                        f"{path.name}/{ecu_name}/iocontrol/{did_str}",
                        "vehicle_states",
                        errors,
                        allowed_states_set,
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
                research_optional = {
                    "priority",
                    "vehicle_states",
                    "created",
                    "updated",
                    "date",
                    "result",
                    "notes",
                    "sources",
                    "what_to_test",
                    "capture_protocol",
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

                    # Reject legacy research prerequisite (renamed vehicle_states)
                    if "prerequisite" in entry:
                        errors.append(
                            f"{label}: legacy field 'prerequisite' — renamed to "
                            "vehicle_states (run scripts/migrate_states_status.py)"
                        )

                    # Validate vehicle_states
                    _validate_state_list(
                        entry.get("vehicle_states"),
                        label,
                        "vehicle_states",
                        errors,
                        allowed_states_set,
                    )

                    stats["research"] += 1

        # Validate routines section (RoutineControl 0x31 discoveries) and
        # iocontrol_discoveries section (IOControl scanner output: UDS 0x2F SF 00
        # with 4-hex-digit DID keys, or KWP2000 0x30 IOCP 00 with 2-hex-digit LID
        # keys). Each entry maps an id to
        # {session, response, nrc, nrc_desc, label, verified, notes}.
        _validate_hit_section(ecu_def, "routines", path, ecu_name, errors, warnings, stats)
        _validate_hit_section(
            ecu_def,
            "iocontrol_discoveries",
            path,
            ecu_name,
            errors,
            warnings,
            stats,
        )

    return errors, warnings, stats


def _validate_identity(
    ecu_def,
    path,
    ecu_name,
    identity_fields,
    valid_protocols,
    valid_confidence,
    errors,
    warnings,
) -> None:
    """Validate the ECU's identity: block (field names, id_protocol, confidence)."""
    identity = ecu_def.get("identity")
    if identity is None:
        return
    if not isinstance(identity, dict):
        errors.append(f"{path.name}/{ecu_name}: 'identity' must be a dict")
        return
    for field in identity:
        if field not in identity_fields:
            warnings.append(f"{path.name}/{ecu_name}/identity: unknown field '{field}'")
    proto = identity.get("id_protocol")
    if proto is not None and proto not in valid_protocols:
        errors.append(
            f"{path.name}/{ecu_name}/identity: invalid id_protocol '{proto}' "
            f"(allowed: {sorted(valid_protocols)})"
        )
    conf = identity.get("identity_confidence")
    if conf is not None and conf not in valid_confidence:
        errors.append(
            f"{path.name}/{ecu_name}/identity: invalid identity_confidence '{conf}' "
            f"(allowed: {sorted(valid_confidence)})"
        )


def _validate_scan_log(ecu_def, path, ecu_name, scan_log_fields, errors, warnings, stats) -> None:
    """Validate the ECU's scan_log: list (probe-history audit)."""
    scan_log = ecu_def.get("scan_log")
    if scan_log is None:
        return
    if not isinstance(scan_log, list):
        errors.append(f"{path.name}/{ecu_name}: 'scan_log' must be a list")
        return
    for i, entry in enumerate(scan_log):
        if not isinstance(entry, dict):
            errors.append(f"{path.name}/{ecu_name}/scan_log[{i}]: entry must be a mapping")
            continue
        for field in entry:
            if field not in scan_log_fields:
                warnings.append(f"{path.name}/{ecu_name}/scan_log[{i}]: unknown field '{field}'")
        stats["scan_log"] += 1


def _validate_dtcs(ecu_def, path, ecu_name, dtcs_fields, errors, warnings, stats) -> None:
    """Validate the ECU's dtcs: map (manufacturer DTC meanings)."""
    dtcs = ecu_def.get("dtcs")
    if dtcs is None:
        return
    if not isinstance(dtcs, dict):
        errors.append(f"{path.name}/{ecu_name}: 'dtcs' must be a dict")
        return
    for code, entry in dtcs.items():
        label = f"{path.name}/{ecu_name}/dtcs/{code}"
        if not isinstance(entry, dict):
            errors.append(f"{label}: entry must be a mapping")
            continue
        for field in entry:
            if field not in dtcs_fields:
                warnings.append(f"{label}: unknown field '{field}'")
        stats["dtcs"] += 1


def _validate_sessions(ecu_def, path, ecu_name, sessions_fields, errors, warnings, stats) -> None:
    """Validate the ECU's sessions: map (diagnostic session types, service 0x10).

    Keys are 0x10 sub-function hex (1-2 digits, no 0x10 prefix). Each entry
    records ``supported`` (bool) and, for unsupported modes, the ``nrc`` that
    was returned. A supported entry must not also carry an ``nrc``; an
    unsupported entry should carry one.
    """
    sessions = ecu_def.get("sessions")
    if sessions is None:
        return
    if not isinstance(sessions, dict):
        errors.append(f"{path.name}/{ecu_name}: 'sessions' must be a dict")
        return
    for mode, entry in sessions.items():
        mode_str = str(mode)
        label = f"{path.name}/{ecu_name}/sessions/{mode_str}"
        # Key must be a 1-2 digit hex session sub-function.
        if not re.fullmatch(r"[0-9A-Fa-f]{1,2}", mode_str):
            errors.append(
                f"{label}: session key must be a 1-2 digit hex 0x10 sub-function "
                f"(e.g. '03' or '81'), got '{mode_str}'"
            )
        if not isinstance(entry, dict):
            errors.append(f"{label}: entry must be a mapping")
            continue
        for field in entry:
            if field not in sessions_fields:
                warnings.append(f"{label}: unknown field '{field}'")
        supported = entry.get("supported")
        if supported is not None and not isinstance(supported, bool):
            errors.append(f"{label}: 'supported' must be a bool")
        has_nrc = entry.get("nrc") is not None
        if supported is True and has_nrc:
            errors.append(f"{label}: supported session must not also carry an 'nrc'")
        if supported is False and not has_nrc:
            warnings.append(f"{label}: unsupported session has no 'nrc' recorded")
        stats["sessions"] += 1


def _validate_hit_section(
    ecu_def: dict,
    section_name: str,
    path: Path,
    ecu_name: str,
    errors: list,
    warnings: list,
    stats: dict,
) -> None:
    """Validate a scanner-generated hit section (routines/iocontrol_discoveries).

    Both sections share the same entry schema: id_hex → {session, response,
    nrc, nrc_desc, label, verified, notes} with session ∈ {default, extended}
    and exactly one of {response, nrc} set.
    """
    section = ecu_def.get(section_name)
    if section is None:
        return
    if not isinstance(section, dict):
        errors.append(f"{path.name}/{ecu_name}: '{section_name}' must be a dict")
        return
    valid_sessions = {"default", "extended"}
    valid_fields = {
        "session",
        "response",
        "nrc",
        "nrc_desc",
        "label",
        "verified",
        "notes",
    }
    for key_id, entry in section.items():
        key_str = str(key_id)
        label = f"{path.name}/{ecu_name}/{section_name}/{key_str}"
        if not isinstance(entry, dict):
            errors.append(f"{label}: entry must be a dict")
            continue
        for field in entry:
            if field not in valid_fields:
                warnings.append(f"{label}: unknown field '{field}'")
        rsession = entry.get("session")
        if rsession is not None and rsession not in valid_sessions:
            errors.append(
                f"{label}: invalid session '{rsession}' (allowed: {sorted(valid_sessions)})"
            )
        has_resp = bool(entry.get("response"))
        has_nrc = entry.get("nrc") is not None
        if not has_resp and not has_nrc:
            warnings.append(f"{label}: neither 'response' nor 'nrc' set")
        stats[section_name] += 1


def validate_meta(path: Path, required_fields: set) -> list[str]:
    """Validate profile.yaml (profile-wide settings)."""
    errors = []
    try:
        with open(path) as f:
            data = yaml.safe_load(f)
    except yaml.YAMLError as e:
        return [f"profile.yaml: YAML parse error: {e}"]

    if not data or not isinstance(data, dict):
        return ["profile.yaml: empty or invalid"]

    for field in required_fields:
        if field not in data:
            errors.append(f"profile.yaml: missing required field '{field}'")

    return errors


def collect_pids_validation(files: list[Path]) -> tuple[list[str], list[str], dict]:
    """Return (errors, warnings, total_stats) for the given ECU yaml files.

    Skips underscore-prefixed files; runs validate_ecu_file on the rest.
    """
    schema = load_schema()

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
        "routines": 0,
        "iocontrol_discoveries": 0,
        "scan_log": 0,
        "dtcs": 0,
        "sessions": 0,
    }

    for fpath in files:
        if fpath.name.startswith("_"):
            continue

        errors, warnings, stats = validate_ecu_file(fpath, schema)
        all_errors.extend(errors)
        all_warnings.extend(warnings)
        for k in total_stats:
            total_stats[k] += stats[k]

    return all_errors, all_warnings, total_stats


def validate_pids_file(fpath: Path) -> tuple[bool, str]:
    errors, warnings, _ = collect_pids_validation([fpath])
    lines = [f"  WARN: {w}" for w in warnings]
    lines += [f"  ERROR: {e}" for e in errors]
    if errors:
        lines.append(f"\n{len(errors)} error(s), {len(warnings)} warning(s)")
    return (not errors, "\n".join(lines))


def _run_pids(files: list[str] | None, stats: bool) -> int:
    from canlib.profile import active

    prof = active()
    if files:
        file_paths = [Path(f) for f in files]
    else:
        file_paths = sorted(prof.ecus_dir.glob("*.yaml"))

    all_errors, all_warnings, total_stats = collect_pids_validation(file_paths)

    # Validate profile.yaml (profile-wide settings) unless a file subset was given.
    if not files:
        required_meta_fields = set(load_schema().get("required_meta_fields", []))
        profile_yaml = prof.root / "profile.yaml"
        if profile_yaml.exists():
            all_errors.extend(validate_meta(profile_yaml, required_meta_fields))

        # Cross-file: an ECU short name (or alias) must be unique across ecus/.
        all_errors.extend(_duplicate_name_errors(file_paths))

    # Print results
    if all_warnings:
        for w in all_warnings:
            print(f"  WARN: {w}")
        print()

    if all_errors:
        for e in all_errors:
            print(f"  ERROR: {e}")
        print(f"\n{len(all_errors)} error(s), {len(all_warnings)} warning(s)")
        return 1

    n_files = len([f for f in file_paths if not f.name.startswith("_")])
    ignored = total_stats["ignored"]
    ignored_str = f", {ignored} ignored" if ignored else ""
    ioctl = total_stats["iocontrol"]
    ioctl_str = f", {ioctl} IOControl DIDs" if ioctl else ""
    research = total_stats["research"]
    research_str = f", {research} research items" if research else ""
    routines = total_stats["routines"]
    routines_str = f", {routines} routines" if routines else ""
    ioctl_disc = total_stats["iocontrol_discoveries"]
    ioctl_disc_str = f", {ioctl_disc} IO discoveries" if ioctl_disc else ""
    dtcs = total_stats["dtcs"]
    dtcs_str = f", {dtcs} DTCs" if dtcs else ""
    print(
        f"OK — {n_files} ECU files, {total_stats['pids']} PIDs{ignored_str}{ioctl_str}{research_str}{routines_str}{ioctl_disc_str}{dtcs_str}, "
        f"{total_stats['params']} parameters "
        f"({total_stats['verified']} verified, {total_stats['unverified']} unverified)"
    )

    if all_warnings:
        print(f"  {len(all_warnings)} warning(s)")

    if stats:
        print(f"\n  ECUs:       {total_stats['ecus']}")
        print(f"  PIDs:       {total_stats['pids']}")
        print(f"  Ignored:    {total_stats['ignored']}")
        print(f"  Parameters: {total_stats['params']}")
        print(f"  Verified:   {total_stats['verified']}")
        print(f"  Unverified: {total_stats['unverified']}")
        print(f"  IOControl:  {total_stats['iocontrol']}")
        print(f"  Research:   {total_stats['research']}")
        print(f"  Routines:   {total_stats['routines']}")
        print(f"  IO Discoveries: {total_stats['iocontrol_discoveries']}")
        print(f"  Scan-log:   {total_stats['scan_log']}")
        print(f"  DTCs:       {total_stats['dtcs']}")
        print(f"  Sessions:   {total_stats['sessions']}")

    return 0


def _duplicate_name_errors(file_paths: list[Path]) -> list[str]:
    """Flag any ECU short name / alias claimed by more than one ecus/ file."""
    errors: list[str] = []
    seen: dict[str, str] = {}
    for fpath in file_paths:
        if fpath.name.startswith("_"):
            continue
        try:
            with open(fpath) as f:
                data = yaml.safe_load(f) or {}
        except (OSError, yaml.YAMLError):
            continue
        if not isinstance(data, dict):
            continue
        for ecu_name, ecu_def in data.items():
            names = [ecu_name]
            if isinstance(ecu_def, dict):
                alias = (ecu_def.get("identity") or {}).get("alias")
                if alias:
                    names.append(alias)
            for nm in names:
                key = str(nm).upper()
                if key in seen and seen[key] != fpath.name:
                    errors.append(
                        f"duplicate ECU name/alias '{nm}' in {fpath.name} (also {seen[key]})"
                    )
                else:
                    seen[key] = fpath.name
    return errors


# ── ecus validation (alias for pids — the per-ECU files are the registry) ──


def _run_ecus() -> int:
    """`canair validate ecus` — the ECU files ARE the registry now, so this
    runs the same per-ECU validation as `validate pids`."""
    return _run_pids(None, stats=False)


# ── captures validation (from validate-captures.py) ───────────────────────


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


def validate_captures_file(path: Path, validator: Validator, rx_addrs: set[int]) -> list[str]:
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
                    errors.append(
                        f"sessions[{si}]: date '{date}' doesn't match filename '{path.stem}'"
                    )

            for ci, cap in enumerate(session.get("captures", []) or []):
                if not isinstance(cap, dict):
                    continue
                ecu = cap.get("ecu")
                if ecu and str(ecu).lower() not in SENTINELS:
                    rx = parse_ecu_ref(ecu)
                    if rx is not None and rx not in rx_addrs:
                        errors.append(
                            f"sessions[{si}].captures[{ci}].ecu: response address "
                            f"'{ecu}' is not a known ECU response address (RX = TX + 8)"
                        )

    return errors


def _run_captures(strict: bool = False) -> int:
    with open(CAPTURES_SCHEMA_FILE) as f:
        schema = json.load(f)
    Draft202012Validator.check_schema(schema)
    validator = Draft202012Validator(schema)
    rx_addrs = load_valid_rx_addrs()

    from canlib.profile import active

    files = sorted(active().captures_dir.glob("*.yaml"))
    files = [f for f in files if f.name != "SCHEMA.yaml"]

    if not files:
        print("No capture files found.")
        return 0

    # State vocabulary for soft warnings (empty when no states.yaml → no warnings).
    from canlib.states import state_names

    vocab = {n.lower() for n in state_names()}

    total_errors = 0
    total_warnings = 0
    total_time_gaps = 0
    for path in files:
        errors = validate_captures_file(path, validator, rx_addrs)
        warnings = _capture_state_warnings(path, vocab) if vocab else []
        warnings += _capture_echo_warnings(path)
        warnings += _capture_nonhex_warnings(path)
        # Missing-time on payload captures: an error under --strict (new-data
        # gate), otherwise a soft warning (existing rows grandfathered).
        time_gaps = _capture_missing_time_warnings(path)
        total_time_gaps += len(time_gaps)
        if strict:
            errors = list(errors) + time_gaps
        else:
            warnings += time_gaps
        if errors:
            print(f"\n{path.name}: {len(errors)} errors")
            for e in errors:
                print(f"  - {e}")
            total_errors += len(errors)
        else:
            print(f"{path.name}: OK")
        for w in warnings:
            print(f"  ⚠ {w}")
        total_warnings += len(warnings)

    if total_warnings:
        print(
            f"\n{total_warnings} warning(s) — see `canair validate states` / echo mismatches above"
        )
    if not strict and total_time_gaps:
        print(
            f"  ({total_time_gaps} untimed payload capture(s); run "
            "`canair validate captures --strict` to treat as errors)"
        )
    if total_errors:
        print(f"\n{total_errors} total errors across {len(files)} files")
        return 1
    else:
        print(f"\nAll {len(files)} files valid.")
        return 0


def _capture_state_warnings(path: Path, vocab: set[str]) -> list[str]:
    """Soft warnings for session vehicle_states outside the declared vocabulary.

    A session's ``vehicle_states`` is a list of tokens (e.g. [ready, parked]); a
    session is flagged only when *none* of its tokens is a known state name
    (case-insensitive). Never an error — this only nudges toward the
    standardized states.yaml vocabulary.
    """
    warnings: list[str] = []
    with open(path) as f:
        data = yaml.safe_load(f)
    if not isinstance(data, dict):
        return warnings
    for si, session in enumerate(data.get("sessions", []) or []):
        if not isinstance(session, dict):
            continue
        states = session.get("vehicle_states")
        if not states:
            continue
        if not isinstance(states, list):
            warnings.append(f"sessions[{si}]: vehicle_states must be a list")
            continue
        tokens = [str(t).strip().lower() for t in states if str(t).strip()]
        if tokens and not any(t in vocab for t in tokens):
            warnings.append(
                f"sessions[{si}]: vehicle_states {states} has no token in the "
                f"states.yaml vocabulary"
            )
    return warnings


def _capture_echo_warnings(path: Path) -> list[str]:
    """Soft warnings for captures whose payload doesn't echo their recorded PID.

    A UDS positive response echoes the request SID (+0x40) and identifier bytes;
    a ``6101…`` payload stored under a ``2102`` request is a stale/misfiled frame
    (the ELM327 leaks a previous request's late response into the next read — see
    ``uds_parse.payload_echo_mismatch``). Reported as a warning, never an error,
    since free-form/raw captures and multi-frame quirks shouldn't hard-fail.
    """
    from canlib.uds_parse import payload_echo_mismatch

    warnings: list[str] = []
    with open(path) as f:
        data = yaml.safe_load(f)
    if not isinstance(data, dict):
        return warnings
    for si, session in enumerate(data.get("sessions", []) or []):
        if not isinstance(session, dict):
            continue
        for ci, cap in enumerate(session.get("captures", []) or []):
            if not isinstance(cap, dict):
                continue
            pid = cap.get("pid")
            payload = cap.get("payload")
            if not pid or not payload:
                continue
            reason = payload_echo_mismatch(str(pid), str(payload))
            if reason:
                warnings.append(
                    f"sessions[{si}].captures[{ci}] ({cap.get('ecu', '?')} {pid} "
                    f"@ {cap.get('time', '?')}): {reason}"
                )
    return warnings


def _capture_nonhex_warnings(path: Path) -> list[str]:
    """Soft warnings for captures whose payload isn't a valid UDS byte string.

    Payloads are recorded by the tool as raw response hex, so a non-hex one
    (an ELM327 status string like ``NO DATA``, free-text notes, or a mixed
    hex+ASCII transcription) signals a mis-recorded capture — see
    ``uds_parse.payload_not_hex``. Reported as a warning, never an error.
    """
    from canlib.uds_parse import payload_not_hex

    warnings: list[str] = []
    with open(path) as f:
        data = yaml.safe_load(f)
    if not isinstance(data, dict):
        return warnings
    for si, session in enumerate(data.get("sessions", []) or []):
        if not isinstance(session, dict):
            continue
        for ci, cap in enumerate(session.get("captures", []) or []):
            if not isinstance(cap, dict):
                continue
            payload = cap.get("payload")
            if not payload:
                continue
            reason = payload_not_hex(str(payload))
            if reason:
                warnings.append(
                    f"sessions[{si}].captures[{ci}] ({cap.get('ecu', '?')} "
                    f"{cap.get('pid', '?')} @ {cap.get('time', '?')}): {reason}"
                )
    return warnings


def _capture_missing_time_warnings(path: Path) -> list[str]:
    """Soft warnings for time-series (``payload``) captures with no usable ``time``.

    A payload capture is a time-series sample and should carry a timestamp so
    cross-signal time-alignment (``canair correlate``/``hunt``) can use it. One-shot
    ``scan_results``/``response`` captures are exempt — a timestamp was never
    meaningful there. Existing untimed payload rows are grandfathered (warning,
    not error, unless ``validate captures --strict``). See Tranche 2.6.
    """
    from canlib.capture_dates import entry_datetime

    warnings: list[str] = []
    with open(path) as f:
        data = yaml.safe_load(f)
    if not isinstance(data, dict):
        return warnings
    for si, session in enumerate(data.get("sessions", []) or []):
        if not isinstance(session, dict):
            continue
        date = session.get("date", "")
        for ci, cap in enumerate(session.get("captures", []) or []):
            if not isinstance(cap, dict) or not cap.get("payload"):
                continue  # only payload (time-series) captures; scans exempt
            # entry_datetime needs the session date + capture time.
            if entry_datetime({"date": date, "time": cap.get("time", "")}) is None:
                warnings.append(
                    f"sessions[{si}].captures[{ci}] ({cap.get('ecu', '?')} "
                    f"{cap.get('pid', '?')}): payload capture has no usable time "
                    "(excluded from time-aligned analysis)"
                )
    return warnings


# ── CLI interface ─────────────────────────────────────────────────────────


def _run_states() -> int:
    """Validate the profile's optional states.yaml (structure + predicates)."""
    from canlib.profile import active
    from canlib.states import StatePredicateError, compile_predicate

    path = active().states_file
    if not path.exists():
        print("No states.yaml (optional) — skipping.")
        return 0

    data = yaml.safe_load(path.read_text()) or {}
    errors: list[str] = []
    if not isinstance(data, dict) or "states" not in data:
        print("states.yaml: missing top-level 'states:' list")
        return 1

    seen: set[str] = set()
    states = data.get("states") or []
    if not isinstance(states, list):
        print("states.yaml: 'states' must be a list")
        return 1

    for i, entry in enumerate(states):
        if not isinstance(entry, dict):
            errors.append(f"states[{i}]: must be a mapping")
            continue
        for extra in set(entry) - {"name", "description", "when"}:
            errors.append(f"states[{i}]: unknown field '{extra}'")
        name = entry.get("name")
        if not name:
            errors.append(f"states[{i}]: missing 'name'")
        elif name in seen:
            errors.append(f"states[{i}]: duplicate state name '{name}'")
        else:
            seen.add(name)
        expr = entry.get("when")
        if expr:
            try:
                compile_predicate(expr)
            except StatePredicateError as ex:
                errors.append(f"states[{i}] ('{name}'): invalid when: {ex}")

    if errors:
        print(f"states.yaml: {len(errors)} errors")
        for e in errors:
            print(f"  - {e}")
        return 1
    print(f"states.yaml: OK ({len(seen)} states)")
    return 0


def add_parser(subparsers):
    parser = subparsers.add_parser(
        NAME,
        help="Check a profile's ecus/, profile.yaml, and captures/ against their schemas",
        description="Validate a profile's data files against their schemas and\n"
        "report problems.\n\n"
        "Pick a target (default: all):\n"
        "  pids      the per-ECU ecus/ files (identity/scan_log/dtcs/pids/...)\n"
        "  captures  the captures/ payload files (+ soft warnings, see below)\n"
        "  ecus      alias for pids\n"
        "  states    states.yaml (vehicle power-state vocabulary + predicates)\n"
        "  all       everything above\n\n"
        "`validate captures` also emits soft warnings for out-of-vocabulary vehicle\n"
        "states, SID/PID/DID echo mismatches (misfiled frames), non-hex payloads\n"
        "(e.g. a stored 'NO DATA'), and untimed payload captures. Pass --strict to\n"
        "promote the untimed-payload warning to an error — the CI / new-data gate.\n\n"
        "Run this after editing ecus/ or adding captures; `canair pids` already\n"
        "validates each edit, so this is the whole-profile check.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""\
examples:
  canair validate                     # validate everything (pids + captures + states)
  canair validate --stats             # + a count summary (ECUs/PIDs/params/verified)
  canair validate pids                # just the ecus/ definition files
  canair validate captures            # just captures/ (with soft warnings)
  canair validate captures --strict   # treat untimed-payload warnings as errors (CI)
  canair validate states              # just states.yaml
  canair validate pids ecus/bms.yaml  # a specific ECU file only
""",
    )
    parser.add_argument(
        "target",
        nargs="?",
        choices=["pids", "captures", "ecus", "states", "all"],
        default="all",
        help="What to validate (default: all)",
    )
    parser.add_argument(
        "files", nargs="*", help="Specific ecus/ files (only with target=pids/ecus)"
    )
    parser.add_argument("--stats", action="store_true", help="Show parameter statistics (pids)")
    parser.add_argument(
        "--strict",
        action="store_true",
        help="Treat soft warnings that gate new data (currently: untimed payload "
        "captures) as errors — for CI / new-capture checks",
    )
    parser.set_defaults(func=run)
    return parser


def run(args) -> int:
    strict = getattr(args, "strict", False)
    if args.target in ("pids", "ecus"):
        return _run_pids(args.files or None, args.stats)
    if args.target == "captures":
        return _run_captures(strict=strict)
    if args.target == "states":
        return _run_states()
    # all: the ecus/ files are validated once via _run_pids (they are the registry).
    rc_p = _run_pids(None, args.stats)
    print()
    rc_c = _run_captures(strict=strict)
    print()
    rc_s = _run_states()
    return rc_p or rc_c or rc_s
