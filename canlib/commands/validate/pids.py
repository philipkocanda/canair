"""pids/ecus validation — per-ECU ecus/ files + profile.yaml vs pids_schema.yaml."""

import re
import sys
from dataclasses import dataclass
from pathlib import Path

import yaml

from canlib.byteindex import wican_to_isotp

from ._common import EXPR_TOKEN_RE, SCHEMA_FILE


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


def _validate_param_type(
    param: dict,
    param_name: str,
    pid: str,
    ecu: str,
    valid_param_types: set,
) -> list[str]:
    """Validate a param's optional typed-decoding fields (type/values/bits/fields).

    Untyped (numeric) params are unaffected — all checks are gated on a declared
    ``type:``. Enforces: a known ``type``; ``values`` only with ``enum`` (int
    keys); ``bits`` only with ``bitmask`` (0-63 int keys); ``fields`` only with
    ``struct`` (list of named sub-params).
    """
    errors: list[str] = []
    ptype = param.get("type")
    where = f"{ecu}/{pid}/{param_name}"

    has_values = "values" in param
    has_bits = "bits" in param
    has_fields = "fields" in param

    if ptype is None:
        # Untyped: companion maps make no sense without a type.
        for fld in ("values", "bits", "fields"):
            if fld in param:
                errors.append(f"{where}: '{fld}' requires a 'type:'")
        return errors

    if ptype not in valid_param_types:
        errors.append(f"{where}: invalid type '{ptype}' (allowed: {sorted(valid_param_types)})")

    if ptype == "enum":
        if not has_values:
            errors.append(f"{where}: type 'enum' requires a 'values:' map")
        elif not isinstance(param["values"], dict):
            errors.append(f"{where}: 'values' must be a mapping of raw_int -> label")
        else:
            for k in param["values"]:
                try:
                    int(k)
                except (ValueError, TypeError):
                    errors.append(f"{where}: enum 'values' key '{k}' must be an integer")
    elif has_values:
        errors.append(f"{where}: 'values' is only valid with type 'enum'")

    if ptype == "bitmask":
        if not has_bits:
            errors.append(f"{where}: type 'bitmask' requires a 'bits:' map")
        elif not isinstance(param["bits"], dict):
            errors.append(f"{where}: 'bits' must be a mapping of bit_index -> label")
        else:
            for k in param["bits"]:
                try:
                    bi = int(k)
                except (ValueError, TypeError):
                    errors.append(f"{where}: bitmask 'bits' key '{k}' must be an integer")
                    continue
                if not 0 <= bi <= 63:
                    errors.append(f"{where}: bitmask 'bits' index {bi} out of range (0-63)")
    elif has_bits:
        errors.append(f"{where}: 'bits' is only valid with type 'bitmask'")

    if ptype == "struct":
        if not has_fields:
            errors.append(f"{where}: type 'struct' requires a 'fields:' list")
        elif not isinstance(param["fields"], list):
            errors.append(f"{where}: 'fields' must be a list of sub-field mappings")
        else:
            for i, sub in enumerate(param["fields"]):
                if not isinstance(sub, dict):
                    errors.append(f"{where}: fields[{i}] must be a mapping")
                elif not sub.get("name"):
                    errors.append(f"{where}: fields[{i}] missing 'name'")
    elif has_fields:
        errors.append(f"{where}: 'fields' is only valid with type 'struct'")

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


def _profile_for_ecu_file(path: Path):
    """Build a Profile rooted at an ECU file's grandparent (``<root>/ecus/x.yaml``).

    Scopes state-vocabulary validation to the profile the file belongs to,
    independent of the globally-active profile. A bare test/tmp file (no
    ``states.yaml`` at that root) simply yields the base ``POWER_STATES``.
    """
    from canlib.profile import Profile

    root = path.resolve().parent.parent
    return Profile(root.name, root)


@dataclass(frozen=True)
class _SchemaFields:
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

    @classmethod
    def from_schema(cls, schema: dict) -> "_SchemaFields":
        required_ecu = set(schema.get("required_ecu_fields", []))
        optional_ecu = set(schema.get("optional_ecu_fields", []))
        required_pid = set(schema.get("required_pid_fields", []))
        optional_pid = set(schema.get("optional_pid_fields", []))
        required_param = set(schema.get("required_param_fields", []))
        identity = schema.get("identity_fields", {}) or {}
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
            valid_pid_status=set(schema.get("valid_pid_status", []))
            or {"active", "draft", "static", "ignored"},
            valid_param_types=set(schema.get("valid_param_types", []))
            or {"numeric", "enum", "bitmask", "ascii", "date", "bcd", "struct"},
        )


def _new_ecu_stats() -> dict:
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


def validate_ecu_file(
    path: Path,
    schema: dict,
    profile=None,
) -> tuple[list[str], list[str], dict]:
    """Validate a single ECU YAML file.

    ``profile`` scopes the accepted vehicle-state vocabulary to that profile's
    ``states.yaml``. When omitted, it is derived from the file's own location
    (an ECU file lives at ``<root>/ecus/<name>.yaml``, so its profile root is the
    grandparent dir) rather than the globally-*active* profile — so validating a
    file in a non-active profile works even when several profiles are discovered
    (avoids a spurious "Multiple profiles found").

    Returns (errors, warnings, stats). The per-section validation is delegated to
    the ``_validate_*`` helpers below; this is the file-level orchestrator.
    """
    from canlib.states import allowed_states

    if profile is None:
        profile = _profile_for_ecu_file(path)
    allowed_states_set = allowed_states(profile)
    fields = _SchemaFields.from_schema(schema)
    stats = _new_ecu_stats()

    try:
        with open(path) as f:
            data = yaml.safe_load(f)
    except yaml.YAMLError as e:
        return [f"{path.name}: YAML parse error: {e}"], [], stats

    if not data or not isinstance(data, dict):
        return [f"{path.name}: empty or invalid YAML"], [], stats

    errors: list[str] = []
    warnings: list[str] = []
    for ecu_name, ecu_def in data.items():
        stats["ecus"] += 1
        _validate_ecu_entry(
            ecu_name, ecu_def, path, fields, allowed_states_set, errors, warnings, stats
        )

    return errors, warnings, stats


def _validate_ecu_entry(
    ecu_name, ecu_def, path, fields: _SchemaFields, allowed_states_set, errors, warnings, stats
) -> None:
    """Validate one ECU definition (the top-level loop body of a file)."""
    if not isinstance(ecu_def, dict):
        errors.append(f"{path.name}/{ecu_name}: ECU definition must be a dict")
        return

    label = f"{path.name}/{ecu_name}"

    # Required / unknown ECU fields
    for field in fields.required_ecu:
        if field not in ecu_def:
            errors.append(f"{label}: missing required field '{field}'")
    for field in ecu_def:
        if field not in fields.all_ecu:
            warnings.append(f"{label}: unknown ECU field '{field}'")

    # Legacy ECU-level availability -> vehicle_states
    if "availability" in ecu_def:
        errors.append(
            f"{label}: legacy field 'availability' — renamed to "
            "vehicle_states (run scripts/migrate_states_status.py)"
        )
    _validate_state_list(
        ecu_def.get("vehicle_states"), label, "vehicle_states", errors, allowed_states_set
    )

    # Validate tx_id
    tx_id = ecu_def.get("tx_id")
    if tx_id is not None and (not isinstance(tx_id, int) or tx_id < 0 or tx_id > 0x7FF):
        errors.append(f"{label}: tx_id must be 0x000-0x7FF, got {tx_id}")

    _validate_identity(
        ecu_def,
        path,
        ecu_name,
        fields.identity,
        fields.valid_protocols,
        fields.valid_confidence,
        errors,
        warnings,
    )
    _validate_scan_log(ecu_def, path, ecu_name, fields.scan_log, errors, warnings, stats)
    _validate_dtcs(ecu_def, path, ecu_name, fields.dtcs, errors, warnings, stats)
    _validate_sessions(ecu_def, path, ecu_name, fields.sessions, errors, warnings, stats)

    # A malformed `pids:` block short-circuits the rest of the ECU (preserves the
    # original loop's `continue`).
    if not _validate_pids(
        ecu_def, path, ecu_name, fields, allowed_states_set, errors, warnings, stats
    ):
        return

    _validate_iocontrol(ecu_def, path, ecu_name, allowed_states_set, errors, warnings, stats)
    _validate_research(ecu_def, path, ecu_name, allowed_states_set, errors, warnings, stats)

    # routines (RoutineControl 0x31) + iocontrol_discoveries (0x2F/0x30 scan
    # output). Each entry maps an id to
    # {session, response, nrc, nrc_desc, label, verified, notes}.
    _validate_hit_section(ecu_def, "routines", path, ecu_name, errors, warnings, stats)
    _validate_hit_section(ecu_def, "iocontrol_discoveries", path, ecu_name, errors, warnings, stats)


def _validate_pids(
    ecu_def, path, ecu_name, fields: _SchemaFields, allowed_states_set, errors, warnings, stats
) -> bool:
    """Validate an ECU's ``pids:`` block. Returns False if it is malformed."""
    pids = ecu_def.get("pids", {})
    if not isinstance(pids, dict):
        errors.append(f"{path.name}/{ecu_name}: 'pids' must be a dict")
        return False
    for pid_code, pid_def in pids.items():
        stats["pids"] += 1
        _validate_one_pid(
            pid_code, pid_def, path, ecu_name, fields, allowed_states_set, errors, warnings, stats
        )
    return True


def _validate_one_pid(
    pid_code,
    pid_def,
    path,
    ecu_name,
    fields: _SchemaFields,
    allowed_states_set,
    errors,
    warnings,
    stats,
) -> None:
    """Validate a single PID definition and (unless ignored) its parameters."""
    pid_str = str(pid_code)
    label = f"{path.name}/{ecu_name}/{pid_str}"

    if not isinstance(pid_def, dict):
        errors.append(f"{label}: PID definition must be a dict")
        return

    for field in fields.required_pid:
        if field not in pid_def:
            errors.append(f"{label}: missing required PID field '{field}'")
    for field in pid_def:
        if field not in fields.all_pid:
            warnings.append(f"{label}: unknown PID field '{field}'")

    # Reject legacy visibility booleans (migrated to `status:`) and the
    # renamed `availability:` — hard cut-over so no file straddles both.
    for legacy, hint in _LEGACY_PID_FIELDS.items():
        if legacy in pid_def:
            errors.append(
                f"{label}: legacy field '{legacy}' — {hint} (run scripts/migrate_states_status.py)"
            )

    period = pid_def.get("period")
    if period is not None and (not isinstance(period, int) or period < 0):
        errors.append(f"{label}: period must be positive int")

    status = pid_def.get("status")
    if status is not None and status not in fields.valid_pid_status:
        errors.append(
            f"{label}: invalid status '{status}' (allowed: {sorted(fields.valid_pid_status)})"
        )

    _validate_state_list(
        pid_def.get("vehicle_states"), label, "vehicle_states", errors, allowed_states_set
    )

    # Ignored PIDs — count and skip parameter validation
    if str(pid_def.get("status", "active")).lower() == "ignored":
        stats["ignored"] += 1
        return

    _validate_parameters(pid_def, path, ecu_name, pid_str, fields, errors, warnings, stats)


def _validate_parameters(
    pid_def, path, ecu_name, pid_str, fields: _SchemaFields, errors, warnings, stats
) -> None:
    """Validate a PID's ``parameters:`` block."""
    params = pid_def.get("parameters", {})
    if not isinstance(params, dict):
        errors.append(f"{path.name}/{ecu_name}/{pid_str}: 'parameters' must be a dict")
        return

    if not params:
        warnings.append(f"{path.name}/{ecu_name}/{pid_str}: no parameters defined")

    for param_name, param in params.items():
        stats["params"] += 1
        label = f"{ecu_name}/{pid_str}/{param_name}"

        if not isinstance(param, dict):
            errors.append(f"{label}: parameter must be a dict")
            continue

        for field in fields.required_param:
            if field not in param:
                errors.append(f"{label}: missing '{field}'")
        for field in param:
            if field not in fields.all_param:
                warnings.append(f"{label}: unknown field '{field}'")

        expr = param.get("expression", "")
        if expr:
            errors.extend(validate_expression(expr, param_name, pid_str, ecu_name))
            warnings.extend(check_pci_bytes(expr, param_name, pid_str, ecu_name))

        # Typed (multi-modal) decoding validation
        errors.extend(
            _validate_param_type(param, param_name, pid_str, ecu_name, fields.valid_param_types)
        )

        if param.get("verified", False):
            stats["verified"] += 1
        else:
            stats["unverified"] += 1


def _validate_iocontrol(
    ecu_def, path, ecu_name, allowed_states_set, errors, warnings, stats
) -> None:
    """Validate an ECU's ``iocontrol:`` block."""
    iocontrol = ecu_def.get("iocontrol")
    if iocontrol is None:
        return
    if not isinstance(iocontrol, dict):
        errors.append(f"{path.name}/{ecu_name}: 'iocontrol' must be a dict")
        return

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
        did_label = f"{path.name}/{ecu_name}/iocontrol/{did}"
        if not isinstance(did_def, dict):
            errors.append(f"{did_label}: must be a dict")
            continue
        if "label" not in did_def:
            warnings.append(f"{did_label}: missing 'label'")
        if "availability" in did_def:
            errors.append(
                f"{did_label}: legacy field 'availability' — renamed to vehicle_states "
                "(run scripts/migrate_states_status.py)"
            )
        for field in did_def:
            if field not in valid_fields:
                warnings.append(f"{did_label}: unknown field '{field}'")
        _validate_state_list(
            did_def.get("vehicle_states"), did_label, "vehicle_states", errors, allowed_states_set
        )
        stats["iocontrol"] += 1


def _validate_research(
    ecu_def, path, ecu_name, allowed_states_set, errors, warnings, stats
) -> None:
    """Validate an ECU's ``research:`` backlog list."""
    research = ecu_def.get("research")
    if research is None:
        return
    if not isinstance(research, list):
        errors.append(f"{path.name}/{ecu_name}: 'research' must be a list")
        return

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

        for field in research_required:
            if field not in entry:
                errors.append(f"{label}: missing required field '{field}'")
        for field in entry:
            if field not in all_research_fields:
                warnings.append(f"{label}: unknown field '{field}'")

        rtype = entry.get("type")
        if rtype and rtype not in valid_types:
            errors.append(f"{label}: invalid type '{rtype}' (allowed: {sorted(valid_types)})")

        rstatus = entry.get("status")
        if rstatus and rstatus not in valid_statuses:
            errors.append(
                f"{label}: invalid status '{rstatus}' (allowed: {sorted(valid_statuses)})"
            )

        rprio = entry.get("priority")
        if rprio and rprio not in valid_priorities:
            errors.append(
                f"{label}: invalid priority '{rprio}' (allowed: {sorted(valid_priorities)})"
            )

        # Reject legacy research prerequisite (renamed vehicle_states)
        if "prerequisite" in entry:
            errors.append(
                f"{label}: legacy field 'prerequisite' — renamed to "
                "vehicle_states (run scripts/migrate_states_status.py)"
            )

        _validate_state_list(
            entry.get("vehicle_states"), label, "vehicle_states", errors, allowed_states_set
        )
        stats["research"] += 1


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


def collect_pids_validation(files: list[Path], profile=None) -> tuple[list[str], list[str], dict]:
    """Return (errors, warnings, total_stats) for the given ECU yaml files.

    Skips underscore-prefixed files; runs validate_ecu_file on the rest.
    ``profile`` scopes the accepted vehicle-state vocabulary (see
    :func:`validate_ecu_file`).
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

        errors, warnings, stats = validate_ecu_file(fpath, schema, profile)
        all_errors.extend(errors)
        all_warnings.extend(warnings)
        for k in total_stats:
            total_stats[k] += stats[k]

    return all_errors, all_warnings, total_stats


def validate_pids_file(fpath: Path, profile=None) -> tuple[bool, str]:
    errors, warnings, _ = collect_pids_validation([fpath], profile)
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
        # Cross-file: a shipped parameter name must be unique across all PIDs
        # (the device turns each into a distinct signal). Caught here, not just
        # at `wican autopid write`.
        all_errors.extend(_duplicate_param_errors(file_paths))

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


def _duplicate_param_errors(file_paths: list[Path]) -> list[str]:
    """Flag a parameter NAME shipped by more than one PID (a device collision).

    Mirrors the wican profile-generation gate (``active`` PID + ``enabled``
    param): those params become distinct signals on the device and their names
    must be globally unique. Catching it here means CI/`validate` fails before
    `wican autopid write` does. See ``wican.generate_profile``.
    """
    from canlib.pids import pid_status

    errors: list[str] = []
    origin: dict[str, str] = {}
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
            if not isinstance(ecu_def, dict):
                continue
            for pid_code, pid_def in (ecu_def.get("pids") or {}).items():
                if not isinstance(pid_def, dict) or pid_status(pid_def) != "active":
                    continue
                for pname, param in (pid_def.get("parameters") or {}).items():
                    if not isinstance(param, dict) or not param.get("enabled", True):
                        continue
                    where = f"{ecu_name} {pid_code}"
                    if pname in origin and origin[pname] != where:
                        errors.append(
                            f"duplicate shipped parameter name '{pname}' in {where} "
                            f"(also {origin[pname]}) — each shipped signal name must be "
                            f"unique across all PIDs (rename one via `canair pids rename-param`)"
                        )
                    else:
                        origin[pname] = where
    return errors


def _run_ecus() -> int:
    """`canair validate ecus` — the ECU files ARE the registry now, so this
    runs the same per-ECU validation as `validate pids`."""
    return _run_pids(None, stats=False)
