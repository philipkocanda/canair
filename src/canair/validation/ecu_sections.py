"""Checks of the smaller ECU sections that the ECU schema cannot make.

The schema checks their shape. What is left: the vehicle-state vocabulary of research entries,
the legacy ``prerequisite:``, identity fields that repeat one value, and entries that record
nothing.
"""

from typing import Any

from ._common import validate_state_list


def entries(section: Any) -> int:
    """How many entries a list or mapping section holds (0 for anything else)."""
    return len(section) if isinstance(section, list | dict) else 0


def validate_research(ecu_def, path, ecu_name, allowed_states_set, errors, warnings, stats) -> None:
    """Check an ECU's ``research:`` backlog against the vocabularies of other files."""
    research = ecu_def.get("research")
    if not isinstance(research, list):
        return
    for i, entry in enumerate(research):
        label = f"{path.name}/{ecu_name}/research[{i}]"
        if not isinstance(entry, dict):
            continue
        stats["research"] += 1
        if "prerequisite" in entry:
            errors.append(
                f"{label}: legacy field 'prerequisite' — renamed to "
                "vehicle_states (run scripts/migrate_states_status.py)"
            )
        validate_state_list(
            entry.get("vehicle_states"), label, "vehicle_states", errors, allowed_states_set
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


def identity_warnings(ecu_def, path, ecu_name) -> list[str]:
    """Warn when two *synonymous* identity fields hold the same value.

    The version-ish identity fields are near-synonyms by necessity (each marque
    names its identity DIDs differently), so the same reading easily gets filed
    under two of them — leaving one of the pair as dead, misleading data. Values
    are compared whitespace-normalized and case-insensitively; empty values are
    ignored.
    """
    identity = ecu_def.get("identity")
    if not isinstance(identity, dict):
        return []
    warnings = []
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
    return warnings


def session_warnings(ecu_def, path, ecu_name) -> list[str]:
    """Warn about an unsupported session (service 0x10) that records no NRC."""
    sessions = ecu_def.get("sessions")
    if not isinstance(sessions, dict):
        return []
    return [
        f"{path.name}/{ecu_name}/sessions/{mode}: unsupported session has no 'nrc' recorded"
        for mode, entry in sessions.items()
        if isinstance(entry, dict) and entry.get("supported") is False and entry.get("nrc") is None
    ]


def hit_warnings(ecu_def: dict, section_name: str, path, ecu_name: str) -> list[str]:
    """Warn about a scanner hit (``routines:``, ``iocontrol_discoveries:``) that records nothing.

    Each hit is either a positive ``response`` or the ``nrc`` that revealed it.
    """
    section = ecu_def.get(section_name)
    if not isinstance(section, dict):
        return []
    return [
        f"{path.name}/{ecu_name}/{section_name}/{key}: neither 'response' nor 'nrc' set"
        for key, entry in section.items()
        if isinstance(entry, dict) and not entry.get("response") and entry.get("nrc") is None
    ]
