"""Surgical, comment-preserving edits to a profile's ``groups.yaml``.

The selector-group vocabulary (``profiles/<name>/groups.yaml``) is a
hand-curated mapping of group name → member selectors — the saved-query analogue
of ``vehicle_states.yaml``/``can_buses.yaml``. These helpers let ``canair
groups`` add/remove/rename a group and edit its description or members without
clobbering the file's comments or layout.

Every write goes through :func:`_safe_write`, which re-parses and re-validates
the file (structure + selector syntax) and reverts on failure — a broken edit
never lands on disk. Group names are normalized to the canonical lower-cased
form; members are written as an inline flow list (``[BMS:2101, OBC]``).
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import TYPE_CHECKING

from ruamel.yaml.comments import CommentedMap, CommentedSeq

from . import yaml_io
from .ecu_groups import normalize_group_name
from .query import QueryError, parse_selector
from .yaml_rt import detect_sequence_indent as _detect_seq
from .yaml_rt import dump as _dump
from .yaml_rt import round_trip_yaml as _yaml

if TYPE_CHECKING:
    from collections.abc import Sequence

    from .profile import Profile

# A group name: a lower-cased word of letters/digits, optionally with internal
# hyphens/underscores (e.g. ``charging``, ``charge-detail``, ``12v``). No spaces
# or leading punctuation — a clean token for the ``@name`` sigil.
_NAME_RE = re.compile(r"^[a-z0-9][a-z0-9_-]*$")


class GroupsEditError(Exception):
    """Raised when a groups.yaml edit cannot be applied safely."""


def _groups_path(profile: Profile | None = None) -> Path:
    from .profile import active

    return (profile or active()).groups_file


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


def _flow_members(members: Sequence[str]) -> CommentedSeq:
    """Render members as an inline flow list (``[BMS:2101, OBC]``)."""
    seq = CommentedSeq(members)
    seq.fa.set_flow_style()
    return seq


def _load_doc(path: Path) -> CommentedMap:
    """Load the groups file round-trip; scaffold an empty doc when absent."""
    if not path.exists():
        doc = CommentedMap()
        doc["groups"] = CommentedMap()
        return doc
    data = _yaml().load(path.read_text())
    if data is None:
        data = CommentedMap()
    if not isinstance(data, dict):
        raise GroupsEditError(f"{path.name}: top-level must be a mapping")
    if "groups" not in data or data["groups"] is None:
        data["groups"] = CommentedMap()
    if not isinstance(data["groups"], dict):
        raise GroupsEditError(f"{path.name}: 'groups:' must be a mapping")
    return data


def _find(groups: dict, name: str) -> str | None:
    """Return the actual key matching ``name`` (case-insensitive), or None."""
    for key in groups:
        if normalize_group_name(key) == name:
            return key
    return None


def _new_entry(description: str | None, members: Sequence[str]) -> CommentedMap:
    entry = CommentedMap()
    if description:
        entry["description"] = description
    entry["members"] = _flow_members(list(members))
    return entry


def _reparse_validate(path: Path) -> None:
    """Re-parse the written file and validate structure + selector syntax."""
    data = yaml_io.safe_load(path.read_text()) or {}
    groups = data.get("groups")
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


def _restore(path: Path, original: str | None) -> None:
    if original is None:
        path.unlink(missing_ok=True)
    else:
        path.write_text(original)


def _safe_write(path: Path, original: str | None, data) -> None:
    """Dump ``data``, then re-parse + re-validate; revert on any failure."""
    seq = _detect_seq(original or "") or (4, 2)
    with open(path, "w") as f:
        _dump(data, f, sequence=seq[0], offset=seq[1])
    try:
        _reparse_validate(path)
    except GroupsEditError:
        _restore(path, original)
        raise
    except Exception as e:  # pragma: no cover - defensive
        _restore(path, original)
        raise GroupsEditError(f"edit failed post-check, reverted: {e}") from e


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
    path = _groups_path(profile)
    original = path.read_text() if path.exists() else None
    data = _load_doc(path)
    if _find(data["groups"], name) is not None:
        raise GroupsEditError(f"group {name!r} already exists")
    data["groups"][name] = _new_entry(description, cleaned)
    _safe_write(path, original, data)
    return path


def remove_group(name: str, *, profile: Profile | None = None) -> Path:
    """Remove a group. Errors if it isn't declared."""
    name = normalize_name(name)
    path = _groups_path(profile)
    if not path.exists():
        raise GroupsEditError("no groups.yaml to edit")
    original = path.read_text()
    data = _load_doc(path)
    key = _find(data["groups"], name)
    if key is None:
        raise GroupsEditError(f"group {name!r} not found")
    del data["groups"][key]
    _safe_write(path, original, data)
    return path


def rename_group(old: str, new: str, *, profile: Profile | None = None) -> Path:
    """Rename a group's key (references in scripts/history are NOT rewritten)."""
    old = normalize_name(old)
    new = normalize_name(new)
    path = _groups_path(profile)
    if not path.exists():
        raise GroupsEditError("no groups.yaml to edit")
    original = path.read_text()
    data = _load_doc(path)
    key = _find(data["groups"], old)
    if key is None:
        raise GroupsEditError(f"group {old!r} not found")
    if old != new and _find(data["groups"], new) is not None:
        raise GroupsEditError(f"group {new!r} already exists")
    # Rebuild the mapping preserving order, swapping the one key.
    rebuilt = CommentedMap()
    for k, v in data["groups"].items():
        rebuilt[new if k == key else k] = v
    data["groups"] = rebuilt
    _safe_write(path, original, data)
    return path


def set_group_description(name: str, value: str | None, *, profile: Profile | None = None) -> Path:
    """Set/clear a group's description."""
    name = normalize_name(name)
    path = _groups_path(profile)
    if not path.exists():
        raise GroupsEditError("no groups.yaml to edit")
    original = path.read_text()
    data = _load_doc(path)
    key = _find(data["groups"], name)
    if key is None:
        raise GroupsEditError(f"group {name!r} not found")
    entry = data["groups"][key]
    if not isinstance(entry, dict):  # legacy bare-list form → promote to mapping
        entry = _new_entry(None, list(entry))
        data["groups"][key] = entry
    if value:
        entry["description"] = value
    elif "description" in entry:
        del entry["description"]
    _safe_write(path, original, data)
    return path


def set_group_members(name: str, members: Sequence[str], *, profile: Profile | None = None) -> Path:
    """Replace a group's member selectors."""
    name = normalize_name(name)
    cleaned = _validate_members(members)
    path = _groups_path(profile)
    if not path.exists():
        raise GroupsEditError("no groups.yaml to edit")
    original = path.read_text()
    data = _load_doc(path)
    key = _find(data["groups"], name)
    if key is None:
        raise GroupsEditError(f"group {name!r} not found")
    entry = data["groups"][key]
    if isinstance(entry, dict):
        entry["members"] = _flow_members(cleaned)
    else:  # legacy bare-list form
        data["groups"][key] = _flow_members(cleaned)
    _safe_write(path, original, data)
    return path
