"""Validation of an ECU's ``pids:`` block: each PID, its fields and its ``parameters:``."""

from canair.dtc.snapshot import did_of_read
from canair.ecus.pids import effective_param, pid_ships, pid_status
from canair.ecus.sensitivity import param_sensitive
from canair.ecus.signal_category import RETIRED_FIELD, category_findings
from canair.uds.support_map import SupportMapError
from canair.uds.support_map import declaration as support_map_declaration

from ._common import validate_shared_fields, validate_state_list
from .expressions import check_pci_bytes, firmware_expression_errors, validate_expression
from .schema_fields import SchemaFields

_RETIRED_HINT = (
    f"{RETIRED_FIELD} is retired in favour of category (stated only where the unit does not "
    "imply it): run canair pids migrate-categories"
)


_LEGACY_PID_FIELDS = {
    "enabled": "use status: draft (PID-level) — enabled is param-level only now",
    "ignored": "use status: ignored",
    "static": "use status: static",
    "availability": "renamed to vehicle_states",
}


def validate_pids_block(
    ecu_def, path, ecu_name, fields: SchemaFields, allowed_states_set, errors, warnings, stats
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


def _snapshot_field_errors(pid_str: str, pid_def: dict, label: str) -> list[str]:
    """``snapshot_only`` must be a boolean on a service-22 DID; ``snapshot_length`` an int >= 1."""
    errors: list[str] = []
    only = pid_def.get("snapshot_only")
    if only is not None and not isinstance(only, bool):
        errors.append(f"{label}: snapshot_only must be a boolean")
    elif only and did_of_read(pid_str) is None:
        errors.append(
            f"{label}: snapshot_only needs a service-22 DID key (22 + 4 hex digits): a DTC "
            "snapshot records data identifiers"
        )
    length = pid_def.get("snapshot_length")
    # `bool` is an `int` subclass, so `snapshot_length: true` would otherwise pass as 1.
    if length is not None and (
        isinstance(length, bool) or not isinstance(length, int) or length < 1
    ):
        errors.append(f"{label}: snapshot_length must be an int >= 1")
    return errors


def _validate_one_pid(
    pid_code,
    pid_def,
    path,
    ecu_name,
    fields: SchemaFields,
    allowed_states_set,
    errors,
    warnings,
    stats,
) -> None:
    """Validate a single PID definition and (unless ignored) its parameters."""
    pid_str = str(pid_code)
    label = f"{path.name}/{ecu_name}/{pid_str}"

    if isinstance(pid_code, int) and len(pid_str) < 4:
        # A request code is a service byte plus an identifier, so at least four hex
        # digits; a shorter integer is what YAML 1.1 makes of a leading-zero key
        # (0105 -> octal 69), and the PID would be requested as "69".
        errors.append(
            f"{label}: PID key parsed as the integer {pid_code} — quote it; YAML reads a "
            'leading-zero key such as 0105 as octal (write "0105":)'
        )
        return

    if not isinstance(pid_def, dict):
        errors.append(f"{label}: PID definition must be a dict")
        return

    for field in fields.required_pid:
        if field not in pid_def:
            errors.append(f"{label}: missing required PID field '{field}'")
    for field in pid_def:
        if field == RETIRED_FIELD:
            warnings.append(f"{label}: {_RETIRED_HINT}")
        elif field not in fields.all_pid:
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
    elif period == 0:
        warnings.append(
            f"{label}: period 0 is not 'never poll' — the firmware reads it as unset and "
            "substitutes a default; give it an explicit interval "
            "(canair pids set-pid-period)"
        )

    variable_length = pid_def.get("variable_length")
    if variable_length is not None and not isinstance(variable_length, bool):
        errors.append(f"{label}: variable_length must be a boolean")
    errors.extend(_snapshot_field_errors(pid_str, pid_def, label))
    if "sensitive" in pid_def and not isinstance(pid_def["sensitive"], bool):
        errors.append(f"{label}: sensitive must be a boolean")

    validate_shared_fields(pid_def, label, errors)
    try:
        support_map_declaration(pid_def)
    except SupportMapError as e:
        errors.append(f"{label}: {e}")

    frames = pid_def.get("response_frames")
    if frames is not None:
        # `bool` is an `int` subclass, so exclude it explicitly — `response_frames:
        # true` would otherwise validate as 1 and ship a one-frame undercount.
        if isinstance(frames, bool) or not isinstance(frames, int) or frames < 1:
            errors.append(f"{label}: response_frames must be an int >= 1")
        elif variable_length:
            errors.append(
                f"{label}: response_frames cannot be set with variable_length: true "
                "(a variable-length response has no fixed frame count)"
            )

    if pid_status(pid_def) == "slow" and "period" not in pid_def:
        errors.append(
            f"{label}: status 'slow' needs an explicit period — without one the device "
            "polls it every 5 s, which is what 'slow' exists to avoid "
            "(canair pids set-pid-period)"
        )

    status = pid_def.get("status")
    if status is not None and status not in fields.valid_pid_status:
        errors.append(
            f"{label}: invalid status '{status}' (allowed: {sorted(fields.valid_pid_status)})"
        )

    validate_state_list(
        pid_def.get("vehicle_states"), label, "vehicle_states", errors, allowed_states_set
    )

    # Ignored PIDs — count and skip parameter validation
    if str(pid_def.get("status", "active")).lower() == "ignored":
        stats["ignored"] += 1
        return

    _validate_parameters(pid_def, path, ecu_name, pid_str, fields, errors, warnings, stats)


def _validate_parameters(
    pid_def, path, ecu_name, pid_str, fields: SchemaFields, errors, warnings, stats
) -> None:
    """Validate a PID's ``parameters:`` block."""
    params = pid_def.get("parameters", {})
    if not isinstance(params, dict):
        errors.append(f"{path.name}/{ecu_name}/{pid_str}: 'parameters' must be a dict")
        return

    if not params:
        warnings.append(f"{path.name}/{ecu_name}/{pid_str}: no parameters defined")

    # Only what ships reaches the device, so only that can sink its table.
    pid_shipped = pid_ships(pid_def)

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
            if field == RETIRED_FIELD:
                warnings.append(f"{label}: {_RETIRED_HINT}")
            elif field not in fields.all_param:
                warnings.append(f"{label}: unknown field '{field}'")

        validate_shared_fields(param, label, errors)
        if "sensitive" in param and not isinstance(param["sensitive"], bool):
            errors.append(f"{label}: sensitive must be a boolean")
        elif param_sensitive(pid_def, param) and "send_to" in param:
            errors.append(
                f"{label}: a sensitive signal never ships to the device, so it cannot "
                "declare send_to"
            )
        category_errors, category_warnings = category_findings(
            effective_param(pid_def, param), label
        )
        errors.extend(category_errors)
        warnings.extend(category_warnings)

        expr = param.get("expression", "")
        ships = pid_shipped and param.get("enabled", True)
        if expr:
            errors.extend(validate_expression(expr, param_name, pid_str, ecu_name))
            if ships:
                errors.extend(firmware_expression_errors(expr, param_name, pid_str, ecu_name))
            warnings.extend(check_pci_bytes(expr, param_name, pid_str, ecu_name, param.get("type")))
        overrides = param.get("per_revision")
        for rid, entry in overrides.items() if isinstance(overrides, dict) else ():
            rexpr = entry.get("expression") if isinstance(entry, dict) else None
            if isinstance(rexpr, str) and rexpr:
                label_r = f"{param_name} ({rid})"
                errors.extend(validate_expression(rexpr, label_r, pid_str, ecu_name))
                if ships:
                    errors.extend(firmware_expression_errors(rexpr, label_r, pid_str, ecu_name))

        # Typed (multi-modal) decoding validation
        errors.extend(
            _validate_param_type(param, param_name, pid_str, ecu_name, fields.valid_param_types)
        )

        if effective_param(pid_def, param).get("verified", False):
            stats["verified"] += 1
        else:
            stats["unverified"] += 1


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
