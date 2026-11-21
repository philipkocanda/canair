"""Comment-preserving, schema-checked edits to a profile's CAN bus vocabulary."""

from __future__ import annotations

from pathlib import Path

from ruamel.yaml.comments import CommentedMap

from canair.core.edit_errors import EditError
from canair.core.json_schema import schema_errors
from canair.core.paths import CAN_BUSES_SCHEMA_FILE, schema_modeline
from canair.core.yaml_rt import folded
from canair.profile.model import Profile

from .doc import Target, edit_doc


class BusEditError(EditError):
    """A CAN bus vocabulary edit could not be applied safely."""


def _check(data: dict | None) -> None:
    errors = schema_errors(CAN_BUSES_SCHEMA_FILE, data)
    if errors:
        raise BusEditError("invalid can_buses.yaml: " + "; ".join(errors))


CAN_BUSES = Target(
    key="can_buses",
    path=lambda profile: profile.can_buses_file,
    error=BusEditError,
    section="can_buses",
    check=_check,
    header=schema_modeline("can_buses") + "\n",
)


def upsert_bus(
    code: str,
    *,
    profile: Profile,
    name: str | None = None,
    description: str | None = None,
    bitrate: int | None = None,
    private: bool | None = None,
) -> Path:
    """Add or update a bus, leaving unspecified fields and other entries unchanged.

    Mapping-form vocabularies are editable; the older list form is refused rather than silently
    discarding comments when converting it to a mapping.
    """
    if not code or code != code.strip():
        raise BusEditError("bus code must not be empty or have surrounding whitespace")
    with edit_doc(CAN_BUSES, profile, create=True) as doc:
        entry = doc.section.get(code)
        if not isinstance(entry, dict):
            # Legacy per-entry strings are names; null is an empty entry.
            if entry is not None and not isinstance(entry, str):
                raise BusEditError(f"bus {code!r} has an invalid entry")
            entry = CommentedMap({"name": entry} if isinstance(entry, str) else {})
            doc.section[code] = entry
        if name is not None:
            entry["name"] = name
        if description is not None:
            entry["description"] = folded(description, key_indent=4, key="description")
        if bitrate is not None:
            entry["bitrate"] = bitrate
        if private is not None:
            entry["private"] = private
    return doc.path
