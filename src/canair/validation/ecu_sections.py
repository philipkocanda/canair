"""Validation of the smaller ECU sections: identity, scan_log, sessions, research and hit lists."""

import re
from pathlib import Path

from ._common import validate_state_list


def validate_research(ecu_def, path, ecu_name, allowed_states_set, errors, warnings, stats) -> None:
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
        "revisions",
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

        validate_state_list(
            entry.get("vehicle_states"), label, "vehicle_states", errors, allowed_states_set
        )
        stats["research"] += 1


def validate_identity(
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
            errors.append(
                f"{path.name}/{ecu_name}/identity: unknown field '{field}' "
                f"(allowed: {sorted(identity_fields)})"
            )
    _warn_duplicate_identity_values(identity, path, ecu_name, warnings)
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


# Identity fields that are near-synonyms of each other, grouped. The same value
# under two fields of ONE group is a redundancy signal (a DID reading mirrored
# into a second field); across groups it is a coincidence — `hw_version: "100"`
# and `sw_version: "100"` describe genuinely different axes and must not warn.
_IDENTITY_SYNONYM_GROUPS = (
    frozenset({"firmware", "fw_version", "sw_version", "sw_id"}),
    frozenset({"hw_version", "hw_sw"}),
    frozenset({"alias", "description"}),
    frozenset({"part_number", "serial", "ecu_id"}),
)


def _warn_duplicate_identity_values(identity, path, ecu_name, warnings) -> None:
    """Warn when two *synonymous* identity fields hold the same value.

    The version-ish identity fields are near-synonyms by necessity (each marque
    names its identity DIDs differently), so the same reading easily gets filed
    under two of them — leaving one of the pair as dead, misleading data. Values
    are compared whitespace-normalized and case-insensitively; empty values are
    ignored.
    """
    for group in _IDENTITY_SYNONYM_GROUPS:
        seen: dict[str, list[str]] = {}
        for field in group:
            norm = " ".join(str(identity.get(field, "")).split()).lower()
            if not norm:
                continue
            seen.setdefault(norm, []).append(field)
        for value, fields in seen.items():
            if len(fields) > 1:
                warnings.append(
                    f"{path.name}/{ecu_name}/identity: fields {sorted(fields)} hold the same "
                    f"value {value!r} — keep the one the ECU actually reported, drop the rest "
                    f"(canair pids rm-identity {ecu_name} <field>)"
                )


def validate_scan_log(ecu_def, path, ecu_name, scan_log_fields, errors, warnings, stats) -> None:
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


def validate_sessions(ecu_def, path, ecu_name, sessions_fields, errors, warnings, stats) -> None:
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


def validate_hit_section(
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
