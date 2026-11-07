"""Validation of one ECU file: the ECU schema, then the checks a schema cannot make.

The ECU schema (``pids_schema.yaml``, published as ``ecus.json``) checks every field's type,
the required fields, the vocabularies that live in it and the rules between two fields of one
mapping; an unknown field is a warning. The section modules then check what depends on another
file, on parsing, or on the addressing mode.
"""

from dataclasses import dataclass
from pathlib import Path

import yaml

from canair.core import yaml_io
from canair.core.id_ranges import parse_id_range
from canair.core.json_schema import schema_findings
from canair.core.paths import ECUS_SCHEMA_FILE
from canair.ecus.can_buses import allowed_can_buses
from canair.ecus.registry import IDENTITY_FIELDS
from canair.profile.model import profile_for_path
from canair.states.vocabulary import allowed_states
from canair.uds.addressing import is_extended, resolve_mode
from canair.uds.protocol import ID_PROTOCOL_VALUES
from canair.variants import VariantsError, effective_meta, load_variants
from canair.variants.revisions import check_ecu as check_revisions

from ._common import duplicate_key_errors, is_int, validate_state_list
from .dtcs import validate_dtcs
from .ecu_sections import (
    entries,
    hit_warnings,
    identity_warnings,
    session_warnings,
    validate_research,
)
from .iocontrol import validate_iocontrol
from .memory import validate_memory
from .pid_defs import validate_pids_block


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


def _can_bus_errors(value, label: str, allowed: set) -> list[str]:
    """Each ``can_bus`` code the profile's ``can_buses.yaml`` does not declare.

    A profile need not declare its buses; then any code is accepted.
    """
    if not allowed or not isinstance(value, list):
        return []
    return [
        f"{label}: invalid can_bus value '{v}' (declared in can_buses.yaml: {sorted(allowed)})"
        for v in value
        if v not in allowed
    ]


def _iocontrol_scan_range_errors(value, label: str) -> list[str]:
    """Each ``iocontrol_scan_ranges`` entry that is not a ``"START-END"`` range of DIDs.

    The resolver (``iocontrol_scan.resolve_iocontrol_ranges``) drops a malformed entry, but a
    definition committed to ``ecus/`` should be well-formed. Both read an entry through
    :func:`canair.core.id_ranges.parse_id_range`.
    """
    if not isinstance(value, list):
        return []
    errors = []
    for spec in value:
        rng = f"{label}: iocontrol_scan_ranges entry '{spec}'"
        try:
            lo, hi = parse_id_range(str(spec))
        except ValueError as e:
            errors.append(f"{rng}: {e}")
            continue
        if not 0 <= lo <= 0xFFFF or not 0 <= hi <= 0xFFFF:
            errors.append(f"{rng} bounds must be within 0000-FFFF")
    return errors


def _declared_variant_ids(profile) -> list[str] | None:
    """The profile's variant IDs, or None when its variants.yaml can't be read."""

    try:
        return list(load_variants(profile))
    except (VariantsError, OSError):
        return None  # reported by `canair validate variants`


@dataclass(frozen=True)
class _Context:
    """What every ECU of one file is checked against."""

    path: Path
    allowed_states: set
    allowed_can_buses: set
    profile_meta: dict


def validate_ecu_file(path: Path, profile=None) -> tuple[list[str], list[str], dict]:
    """Validate a single ECU YAML file.

    ``profile`` scopes the accepted vehicle-state vocabulary to that profile's
    ``vehicle_states.yaml``. When omitted, it is derived from the file's own location
    (an ECU file lives at ``<root>/ecus/<name>.yaml``, so its profile root is the
    grandparent dir) rather than the globally-*active* profile — so validating a
    file in a non-active profile works even when several profiles are discovered
    (avoids a spurious "Multiple profiles found").

    Returns (errors, warnings, stats): the schema's findings first, then the rest.
    """

    if profile is None:
        profile = profile_for_path(path)
    stats = new_ecu_stats()

    try:
        text = path.read_text()
        data = yaml_io.safe_load(text)
    except yaml.YAMLError as e:
        return [f"{path.name}: YAML parse error: {e}"], [], stats

    if not data or not isinstance(data, dict):
        return [f"{path.name}: empty or invalid YAML"], [], stats

    schema_errs, schema_warns = schema_findings(ECUS_SCHEMA_FILE, data)
    errors = [f"{path.name}: {e}" for e in duplicate_key_errors(text) + schema_errs]
    warnings = [f"{path.name}: {w}" for w in schema_warns]
    context = _Context(
        path=path,
        allowed_states=allowed_states(profile),
        allowed_can_buses=allowed_can_buses(profile),
        profile_meta=effective_meta(profile),
    )
    variant_ids = _declared_variant_ids(profile)
    for ecu_name, ecu_def in data.items():
        stats["ecus"] += 1
        if not isinstance(ecu_def, dict):
            continue
        errors += [
            f"{path.name}/{e}"
            for e in check_revisions(
                ecu_name,
                ecu_def,
                variant_ids=variant_ids,
                identity_fields=set(IDENTITY_FIELDS),
                valid_protocols=set(ID_PROTOCOL_VALUES),
            )
        ]
        _validate_ecu_entry(ecu_name, ecu_def, context, errors, warnings, stats)

    return errors, warnings, stats


def _validate_ecu_entry(ecu_name, ecu_def: dict, ctx: _Context, errors, warnings, stats) -> None:
    """Check one ECU definition (the top-level loop body of a file)."""
    path = ctx.path
    label = f"{path.name}/{ecu_name}"

    if "availability" in ecu_def:
        errors.append(
            f"{label}: legacy field 'availability' — renamed to "
            "vehicle_states (run scripts/migrate_states_status.py)"
        )
    validate_state_list(
        ecu_def.get("vehicle_states"), label, "vehicle_states", errors, ctx.allowed_states
    )
    errors += _can_bus_errors(ecu_def.get("can_bus"), label, ctx.allowed_can_buses)
    errors += _iocontrol_scan_range_errors(ecu_def.get("iocontrol_scan_ranges"), label)

    # The id width follows this ECU's addressing mode (per-ECU addressing.mode → profile →
    # 11-bit): 11-bit (0x7FF) or 29-bit (0x1FFFFFFF). The schema checks the 29-bit limit.
    mode = resolve_mode(ctx.profile_meta, ecu_def)
    if not is_extended(mode):
        for field in ("tx_id", "rx_id"):
            value = ecu_def.get(field)
            if is_int(value) and value > 0x7FF:
                errors.append(f"{label}: {field} must be 0x000-0x7FF in {mode}, got 0x{value:X}")

    warnings += identity_warnings(ecu_def, path, ecu_name)
    warnings += session_warnings(ecu_def, path, ecu_name)
    for section in ("scan_log", "sessions"):
        stats[section] += entries(ecu_def.get(section))
    validate_dtcs(ecu_def, path, ecu_name, warnings, stats)

    # A malformed `pids:` block short-circuits the rest of the ECU.
    if not validate_pids_block(
        ecu_def, path, ecu_name, ctx.allowed_states, errors, warnings, stats
    ):
        return

    validate_iocontrol(
        ecu_def, path, ecu_name, ctx.allowed_states, errors, warnings, stats, ctx.profile_meta
    )
    validate_research(ecu_def, path, ecu_name, ctx.allowed_states, errors, warnings, stats)

    # routines (RoutineControl 0x31) + iocontrol_discoveries (0x2F/0x30 scan output).
    for section in ("routines", "iocontrol_discoveries"):
        warnings += hit_warnings(ecu_def, section, path, ecu_name)
        stats[section] += entries(ecu_def.get(section))
    # memory: (ReadMemoryByAddress format and scan map) and the memory PIDs.
    validate_memory(ecu_def, path, ecu_name, errors, warnings)
