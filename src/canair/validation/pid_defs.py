"""Checks of an ECU's ``pids:`` block that the ECU schema cannot make.

The schema checks each PID's and signal's fields, types and the rules between two of them
(``type: enum`` needs ``values:``). What is left is here: a PID key YAML read as an octal number,
legacy fields with their migration, the vehicle-state vocabulary, support maps, expressions,
categories, and the rules that follow what a PID passes on to its signals.
"""

from canair.dtc.snapshot import did_of_read
from canair.ecus.pid_status import pid_ships
from canair.ecus.pids import effective_param
from canair.ecus.sensitivity import param_sensitive
from canair.ecus.shipping import param_ships
from canair.ecus.signal_category import RETIRED_FIELD, category_findings
from canair.uds.support_map import SupportMapError
from canair.uds.support_map import declaration as support_map_declaration

from ._common import validate_state_list
from .expressions import check_pci_bytes, firmware_expression_errors, validate_expression

_RETIRED_HINT = (
    f"{RETIRED_FIELD} is retired in favour of category (stated only where the unit does not "
    "imply it): run canair pids migrate-categories"
)


_RENAMED_PARAM_FIELDS = {
    "enabled": "renamed to ship (enabled: false is now ship: false); this field is ignored",
}

_LEGACY_PID_FIELDS = {
    "enabled": "use status: draft (PID-level); a signal states ship: false instead",
    "ignored": "use status: ignored",
    "static": "use status: static",
    "availability": "renamed to vehicle_states",
}


def validate_pids_block(
    ecu_def, path, ecu_name, allowed_states_set, errors, warnings, stats
) -> bool:
    """Check an ECU's ``pids:`` block. Returns False if it is not a mapping."""
    pids = ecu_def.get("pids", {})
    if not isinstance(pids, dict):
        return False
    for pid_code, pid_def in pids.items():
        stats["pids"] += 1
        _validate_one_pid(
            pid_code, pid_def, path, ecu_name, allowed_states_set, errors, warnings, stats
        )
    return True


def _validate_one_pid(
    pid_code, pid_def, path, ecu_name, allowed_states_set, errors, warnings, stats
) -> None:
    """Check a single PID definition and (unless ignored) its signals."""
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
        return

    if RETIRED_FIELD in pid_def:
        warnings.append(f"{label}: {_RETIRED_HINT}")
    # Reject legacy visibility booleans (migrated to `status:`) and the
    # renamed `availability:` — hard cut-over so no file straddles both.
    for legacy, hint in _LEGACY_PID_FIELDS.items():
        if legacy in pid_def:
            errors.append(
                f"{label}: legacy field '{legacy}' — {hint} (run scripts/migrate_states_status.py)"
            )

    if pid_def.get("period") == 0 and not isinstance(pid_def.get("period"), bool):
        warnings.append(
            f"{label}: period 0 is not 'never poll' — the firmware reads it as unset and "
            "substitutes a default; give it an explicit interval "
            "(canair pids set-pid-period)"
        )

    if pid_def.get("snapshot_only") is True and did_of_read(pid_str) is None:
        errors.append(
            f"{label}: snapshot_only needs a service-22 DID key (22 + 4 hex digits): a DTC "
            "snapshot records data identifiers"
        )

    try:
        support_map_declaration(pid_def)
    except SupportMapError as e:
        errors.append(f"{label}: {e}")

    validate_state_list(
        pid_def.get("vehicle_states"), label, "vehicle_states", errors, allowed_states_set
    )

    # Ignored PIDs — count and skip the signals
    if str(pid_def.get("status", "active")).lower() == "ignored":
        stats["ignored"] += 1
        return

    _validate_parameters(pid_def, path, ecu_name, pid_str, errors, warnings, stats)


def _validate_parameters(pid_def, path, ecu_name, pid_str, errors, warnings, stats) -> None:
    """Check a PID's ``parameters:`` block."""
    params = pid_def.get("parameters", {})
    if not isinstance(params, dict):
        return

    if not params:
        warnings.append(f"{path.name}/{ecu_name}/{pid_str}: no parameters defined")

    # Only what ships reaches the device, so only that can sink its table.
    pid_shipped = pid_ships(pid_def, pid_str)

    for param_name, param in params.items():
        stats["params"] += 1
        label = f"{ecu_name}/{pid_str}/{param_name}"

        if not isinstance(param, dict):
            continue

        if RETIRED_FIELD in param:
            warnings.append(f"{label}: {_RETIRED_HINT}")
        for field, hint in _RENAMED_PARAM_FIELDS.items():
            if field in param:
                warnings.append(f"{label}: '{field}' {hint}")

        if param_sensitive(pid_def, param) and "send_to" in param:
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
        ships = pid_shipped and param_ships(param)
        if isinstance(expr, str) and expr:
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

        if effective_param(pid_def, param).get("verified", False):
            stats["verified"] += 1
        else:
            stats["unverified"] += 1
