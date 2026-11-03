"""Surgical, comment-preserving edits to a profile's ``groups.yaml``.

The selector-group vocabulary (``profiles/<name>/groups.yaml``) is a
hand-curated mapping of group name → member selectors — the saved-query analogue
of ``vehicle_states.yaml``/``can_buses.yaml``. These helpers let ``canair
groups`` add/remove/rename a group and edit its description or members without
clobbering the file's comments or layout.

Every write goes through :func:`canlib.yaml_edit.doc.edit_doc`, which re-parses and validates
the file (structure + selector syntax) and reverts on failure — a broken edit
never lands on disk. Group names are normalized to the canonical lower-cased
form; members are written as an inline flow list (``[BMS:2101, OBC]``).
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from pathlib import Path
from typing import TYPE_CHECKING

from ruamel.yaml.comments import CommentedMap

from canlib.ecus.ecu_groups import normalize_group_name
from canlib.query import QueryError, parse_selector

from .doc import Target, edit_doc
from .errors import EditError
from .ruamel_ops import find_key, flow_seq, remove_key, rename_key

if TYPE_CHECKING:
    from canlib.profile import Profile

# A group name: a lower-cased word of letters/digits, optionally with internal
# hyphens/underscores (e.g. ``charging``, ``charge-detail``, ``12v``). No spaces
# or leading punctuation — a clean token for the ``@name`` sigil.
_NAME_RE = re.compile(r"^[a-z0-9][a-z0-9_-]*$")


class GroupsEditError(EditError):
    """Raised when a groups.yaml edit cannot be applied safely."""


def normalize_name(name: str) -> str:
    """Normalize + validate a group name to the canonical lower-cased form."""
    norm = normalize_group_name(name)
    if not norm:
        raise GroupsEditError("group name must not be empty")
    if not _NAME_RE.match(norm):
        raise GroupsEditError(
            f"invalid group name {name!r} — a group name is lower-case "
            "letters/digits with optional internal '-'/'_' (no spaces or "
            "leading punctuation)"
        )
    return norm


def _validate_members(members: Sequence[str]) -> list[str]:
    """Validate + normalize member selectors; return the cleaned token list."""
    cleaned: list[str] = []
    for raw in members:
        tok = str(raw).strip()
        if not tok:
            continue
        if tok.startswith("@"):
            raise GroupsEditError(
                f"member {tok!r} — groups cannot contain other groups; list "
                "plain ECU / ECU:PID selectors"
            )
        try:
            parse_selector(tok)  # raises QueryError on a malformed selector
        except QueryError as e:
            raise GroupsEditError(f"invalid member selector {tok!r}: {e}") from None
        cleaned.append(tok)
    if not cleaned:
        raise GroupsEditError("a group needs at least one member selector")
    return cleaned


def _new_entry(description: str | None, members: Sequence[str]) -> CommentedMap:
    entry = CommentedMap()
    if description:
        entry["description"] = description
    entry["members"] = flow_seq(members)
    return entry


def _check(data: dict | None) -> None:
    """Validate the re-parsed file: structure and selector syntax."""
    groups = (data or {}).get("groups")
    if not isinstance(groups, dict):
        raise GroupsEditError("top-level 'groups:' must be a mapping after edit")
    seen: set[str] = set()
    for name, meta in groups.items():
        key = normalize_group_name(name)
        if key in seen:
            raise GroupsEditError(f"duplicate group name {key!r} after edit")
        seen.add(key)
        members = meta.get("members") if isinstance(meta, dict) else meta
        if not isinstance(members, list) or not members:
            raise GroupsEditError(f"group {key!r} needs a non-empty members list after edit")
        _validate_members(members)


GROUPS = Target(
    key="groups",
    path=lambda profile: profile.groups_file,
    error=GroupsEditError,
    section="groups",
    check=_check,
)


def _key(doc, name: str) -> str:
    key = find_key(doc.section, name, normalize_group_name)
    if key is None:
        raise GroupsEditError(f"group {name!r} not found")
    return key


# ── public editors ──────────────────────────────────────────────────────────


def add_group(
    name: str,
    members: Sequence[str],
    *,
    description: str | None = None,
    profile: Profile | None = None,
) -> Path:
    """Add a new group (scaffolds the file if absent). Errors if it exists."""
    name = normalize_name(name)
    cleaned = _validate_members(members)
    with edit_doc(GROUPS, profile, create=True) as doc:
        if find_key(doc.section, name, normalize_group_name) is not None:
            raise GroupsEditError(f"group {name!r} already exists")
        doc.section[name] = _new_entry(description, cleaned)
    return doc.path


def remove_group(name: str, *, profile: Profile | None = None) -> Path:
    """Remove a group. Errors if it isn't declared."""
    name = normalize_name(name)
    with edit_doc(GROUPS, profile) as doc:
        remove_key(doc.section, _key(doc, name), parent=doc.data, parent_key="groups")
    return doc.path


def rename_group(old: str, new: str, *, profile: Profile | None = None) -> Path:
    """Rename a group's key (references in scripts/history are NOT rewritten)."""
    old = normalize_name(old)
    new = normalize_name(new)
    with edit_doc(GROUPS, profile) as doc:
        key = _key(doc, old)
        if old != new and find_key(doc.section, new, normalize_group_name) is not None:
            raise GroupsEditError(f"group {new!r} already exists")
        rename_key(doc.section, key, new)
    return doc.path


def set_group_description(name: str, value: str | None, *, profile: Profile | None = None) -> Path:
    """Set/clear a group's description."""
    name = normalize_name(name)
    with edit_doc(GROUPS, profile) as doc:
        key = _key(doc, name)
        entry = doc.section[key]
        if not isinstance(entry, dict):  # legacy bare-list form → promote to mapping
            entry = _new_entry(None, list(entry))
            doc.section[key] = entry
        if value:
            entry["description"] = value
        elif "description" in entry:
            del entry["description"]
    return doc.path


def set_group_members(name: str, members: Sequence[str], *, profile: Profile | None = None) -> Path:
    """Replace a group's member selectors."""
    name = normalize_name(name)
    cleaned = _validate_members(members)
    with edit_doc(GROUPS, profile) as doc:
        key = _key(doc, name)
        entry = doc.section[key]
        if isinstance(entry, dict):
            entry["members"] = flow_seq(cleaned)
        else:  # legacy bare-list form
            doc.section[key] = flow_seq(cleaned)
    return doc.path
