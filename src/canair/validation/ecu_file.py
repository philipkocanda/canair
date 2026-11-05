"""Validation of one ECU file: its top-level fields, then each section by its own module."""

from pathlib import Path
from typing import Any

import yaml

from canair.core import yaml_io
from canair.core.id_ranges import parse_id_range
from canair.variants import effective_meta
from canair.variants.revisions import check_ecu as check_revisions

from ._common import duplicate_key_errors, is_int, validate_state_list
from .addressing import validate_ecu_addressing
from .dtcs import validate_dtcs
from .ecu_sections import (
    validate_hit_section,
    validate_identity,
    validate_research,
    validate_scan_log,
    validate_sessions,
)
from .iocontrol import validate_iocontrol
from .isotp import validate_ecu_isotp
from .memory import validate_memory
from .pid_defs import validate_pids_block
from .schema_fields import SchemaFields, new_ecu_stats


def _validate_can_bus_list(value, label: str, errors: list, allowed: set) -> None:
    """Validate an ECU's top-level ``can_bus`` list.

    Enforces list shape and no duplicates. Codes are checked against the
    profile's declared vocabulary (``can_buses.yaml``) only when that vocabulary
    is non-empty — a profile need not declare its buses, in which case any code
    is accepted (membership can't be checked).
    """
    if value is None:
        return
    if not isinstance(value, list):
        errors.append(f"{label}: can_bus must be a list")
        return
    if allowed:
        for v in value:
            if v not in allowed:
                errors.append(
                    f"{label}: invalid can_bus value '{v}' "
                    f"(declared in can_buses.yaml: {sorted(allowed)})"
                )
    if len(value) != len(set(value)):
        errors.append(f"{label}: duplicate can_bus values")


def _allowed_can_buses(profile) -> set:
    """The profile's declared CAN bus vocabulary (empty when undeclared)."""
    from canair.ecus.can_buses import allowed_can_buses

    return allowed_can_buses(profile)


def _validate_iocontrol_scan_ranges(value, label: str, errors: list) -> None:
    """Validate an ECU's optional ``iocontrol_scan_ranges`` list.

    Each entry must be a ``"START-END"`` hex string with ``start <= end`` and
    both in the 16-bit DID space. Shape-only — the resolver
    (``iocontrol_scan.resolve_iocontrol_ranges``) tolerates a malformed entry by
    dropping it, but a definition committed to ``ecus/`` should be well-formed.
    Both read an entry through :func:`canair.core.id_ranges.parse_id_range`.
    """
    if value is None:
        return
    if not isinstance(value, list):
        errors.append(f"{label}: iocontrol_scan_ranges must be a list of 'START-END' hex strings")
        return
    for spec in value:
        rng = f"{label}: iocontrol_scan_ranges entry '{spec}'"
        try:
            lo, hi = parse_id_range(str(spec))
        except ValueError as e:
            errors.append(f"{rng}: {e}")
            continue
        if not 0 <= lo <= 0xFFFF or not 0 <= hi <= 0xFFFF:
            errors.append(f"{rng} bounds must be within 0000-FFFF")


def _profile_for_ecu_file(path: Path):
    """The profile owning an ECU file (``<root>/ecus/x.yaml``).

    Scopes state-vocabulary validation to the profile the file belongs to,
    independent of the globally-active profile. A bare test/tmp file (no
    ``vehicle_states.yaml`` at that root) simply yields the base ``POWER_STATES``.
    """
    from canair.profile import profile_for_path

    return profile_for_path(path)


def _declared_variant_ids(profile) -> list[str] | None:
    """The profile's variant IDs, or None when its variants.yaml can't be read."""
    from canair.variants import VariantsError, load_variants

    try:
        return list(load_variants(profile))
    except (VariantsError, OSError):
        return None  # reported by `canair validate variants`


def _validate_wake(wake: Any, label: str, fields: "SchemaFields", errors: list[str]) -> None:
    """Validate an ECU's optional ``wake:`` block (canair.ecus.wake wake ritual).

    Enforces a mapping shape, known fields, a required + known ``method``, and
    sane types on the numeric knobs. Value semantics (interval < sleep timer) are
    left to the resolver's tolerant defaults — this catches structural mistakes.
    """
    if wake is None:
        return
    if not isinstance(wake, dict):
        errors.append(f"{label}: 'wake' must be a mapping")
        return

    for key in wake:
        if key not in fields.wake:
            errors.append(
                f"{label}: unknown wake field '{key}' (allowed: {', '.join(sorted(fields.wake))})"
            )
    method = wake.get("method")
    if method is None:
        errors.append(
            f"{label}: 'wake' requires a 'method' (one of {sorted(fields.valid_wake_methods)})"
        )
    elif method not in fields.valid_wake_methods:
        errors.append(
            f"{label}: 'wake.method' must be one of "
            f"{', '.join(sorted(fields.valid_wake_methods))}, got {method!r}"
        )
    for key in ("attempts", "interval_ms", "sleep_timer_ms"):
        if key in wake and not (is_int(wake[key]) and wake[key] >= 0):
            errors.append(f"{label}: 'wake.{key}' must be a non-negative integer")
    if "attempts" in wake and is_int(wake["attempts"]) and wake["attempts"] < 1:
        errors.append(f"{label}: 'wake.attempts' must be at least 1")


def validate_ecu_file(
    path: Path,
    schema: dict,
    profile=None,
) -> tuple[list[str], list[str], dict]:
    """Validate a single ECU YAML file.

    ``profile`` scopes the accepted vehicle-state vocabulary to that profile's
    ``vehicle_states.yaml``. When omitted, it is derived from the file's own location
    (an ECU file lives at ``<root>/ecus/<name>.yaml``, so its profile root is the
    grandparent dir) rather than the globally-*active* profile — so validating a
    file in a non-active profile works even when several profiles are discovered
    (avoids a spurious "Multiple profiles found").

    Returns (errors, warnings, stats). The per-section validation is delegated to
    the ``_validate_*`` helpers below; this is the file-level orchestrator.
    """
    from canair.states import allowed_states

    if profile is None:
        profile = _profile_for_ecu_file(path)
    allowed_states_set = allowed_states(profile)
    allowed_can_buses_set = _allowed_can_buses(profile)
    profile_meta = effective_meta(profile)
    fields = SchemaFields.from_schema(schema)
    variant_ids = _declared_variant_ids(profile)
    stats = new_ecu_stats()

    try:
        text = path.read_text()
        data = yaml_io.safe_load(text)
    except yaml.YAMLError as e:
        return [f"{path.name}: YAML parse error: {e}"], [], stats

    if not data or not isinstance(data, dict):
        return [f"{path.name}: empty or invalid YAML"], [], stats

    errors: list[str] = [f"{path.name}: {e}" for e in duplicate_key_errors(text)]
    warnings: list[str] = []
    for ecu_name, ecu_def in data.items():
        stats["ecus"] += 1
        if isinstance(ecu_def, dict):
            errors += [
                f"{path.name}/{e}"
                for e in check_revisions(
                    ecu_name,
                    ecu_def,
                    variant_ids=variant_ids,
                    identity_fields=fields.identity,
                    valid_protocols=fields.valid_protocols,
                )
            ]
        _validate_ecu_entry(
            ecu_name,
            ecu_def,
            path,
            fields,
            allowed_states_set,
            allowed_can_buses_set,
            profile_meta,
            errors,
            warnings,
            stats,
        )

    return errors, warnings, stats


def _validate_ecu_entry(
    ecu_name,
    ecu_def,
    path,
    fields: SchemaFields,
    allowed_states_set,
    allowed_can_buses_set,
    profile_meta,
    errors,
    warnings,
    stats,
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

    if "multi_did_max" in ecu_def and (
        not isinstance(ecu_def["multi_did_max"], int)
        or isinstance(ecu_def["multi_did_max"], bool)
        or ecu_def["multi_did_max"] < 1
    ):
        errors.append(f"{label}: 'multi_did_max' must be a positive integer")

    # Legacy ECU-level availability -> vehicle_states
    if "availability" in ecu_def:
        errors.append(
            f"{label}: legacy field 'availability' — renamed to "
            "vehicle_states (run scripts/migrate_states_status.py)"
        )
    validate_state_list(
        ecu_def.get("vehicle_states"), label, "vehicle_states", errors, allowed_states_set
    )

    # can_bus: physical CAN bus segment(s), validated against the profile's
    # declared vocabulary (can_buses.yaml) — vendor-specific, so per-profile.
    _validate_can_bus_list(ecu_def.get("can_bus"), label, errors, allowed_can_buses_set)

    # iocontrol_scan_ranges: optional list of "START-END" hex DID ranges the
    # 0x2F scanner sweeps on this ECU (replaces the old hardcoded HK zones).
    _validate_iocontrol_scan_ranges(ecu_def.get("iocontrol_scan_ranges"), label, errors)

    # wake: how to rouse a fast-sleeping ECU before reads (canair.ecus.wake).
    _validate_wake(ecu_def.get("wake"), label, fields, errors)

    # Resolve this ECU's addressing mode (per-ECU addressing.mode → profile → 11-bit)
    # to pick the valid arbitration-id width: 11-bit (0x7FF) vs 29-bit (0x1FFFFFFF).
    from canair.uds.addressing import is_extended, resolve_mode

    validate_ecu_addressing(ecu_def.get("addressing"), label, errors)
    validate_ecu_isotp(ecu_def.get("isotp"), label, errors)
    mode = resolve_mode(profile_meta, ecu_def)
    max_id = 0x1FFFFFFF if is_extended(mode) else 0x7FF
    width = "0x00000000-0x1FFFFFFF (29-bit)" if is_extended(mode) else "0x000-0x7FF"

    # Validate tx_id
    tx_id = ecu_def.get("tx_id")
    if tx_id is not None and (
        not isinstance(tx_id, int) or isinstance(tx_id, bool) or tx_id < 0 or tx_id > max_id
    ):
        errors.append(f"{label}: tx_id must be {width}, got {tx_id}")

    # Validate rx_id (optional CAN response-address override)
    rx_id = ecu_def.get("rx_id")
    if rx_id is not None and (
        not isinstance(rx_id, int) or isinstance(rx_id, bool) or rx_id < 0 or rx_id > max_id
    ):
        errors.append(f"{label}: rx_id must be {width}, got {rx_id}")

    validate_identity(
        ecu_def,
        path,
        ecu_name,
        fields.identity,
        fields.valid_protocols,
        fields.valid_confidence,
        errors,
        warnings,
    )
    validate_scan_log(ecu_def, path, ecu_name, fields.scan_log, errors, warnings, stats)
    validate_dtcs(ecu_def, path, ecu_name, fields.dtcs, errors, warnings, stats)
    validate_sessions(ecu_def, path, ecu_name, fields.sessions, errors, warnings, stats)

    # A malformed `pids:` block short-circuits the rest of the ECU (preserves the
    # original loop's `continue`).
    if not validate_pids_block(
        ecu_def, path, ecu_name, fields, allowed_states_set, errors, warnings, stats
    ):
        return

    validate_iocontrol(
        ecu_def, path, ecu_name, allowed_states_set, errors, warnings, stats, profile_meta
    )
    validate_research(ecu_def, path, ecu_name, allowed_states_set, errors, warnings, stats)

    # routines (RoutineControl 0x31) + iocontrol_discoveries (0x2F/0x30 scan
    # output). Each entry maps an id to
    # {session, response, nrc, nrc_desc, label, verified, notes}.
    validate_hit_section(ecu_def, "routines", path, ecu_name, errors, warnings, stats)
    validate_hit_section(ecu_def, "iocontrol_discoveries", path, ecu_name, errors, warnings, stats)
    # memory: (ReadMemoryByAddress format and scan map) and the memory PIDs.
    validate_memory(ecu_def, path, ecu_name, errors, warnings)
