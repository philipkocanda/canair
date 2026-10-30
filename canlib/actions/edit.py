"""Surgical, comment-preserving edits to a profile's ``actions.yaml``.

Every write re-parses the file with the same checks the loader applies
(:func:`canlib.actions.parse_actions_doc`) and reverts on failure, so an edit can
never leave an unguarded or malformed action on disk. Lists are written as inline
flow sequences and each request as a flow mapping, the layout the loader expects
a human to read.
"""

from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path
from typing import TYPE_CHECKING

from ruamel.yaml.comments import CommentedMap, CommentedSeq

from canlib import yaml_io
from canlib.actions import (
    ActionError,
    ActionRequest,
    normalize_name,
    parse_action,
    parse_actions_doc,
)
from canlib.yaml_rt import detect_sequence_indent as _detect_seq
from canlib.yaml_rt import dump as _dump
from canlib.yaml_rt import folded as _folded
from canlib.yaml_rt import round_trip_yaml as _yaml

if TYPE_CHECKING:
    from canlib.profile import Profile


def _actions_path(profile: Profile | None) -> Path:
    from canlib.profile import active

    return (profile or active()).actions_file


def _flow_list(items: Sequence[str]) -> CommentedSeq:
    seq = CommentedSeq(list(items))
    seq.fa.set_flow_style()
    return seq


def _request_map(req: ActionRequest) -> CommentedMap:
    entry = CommentedMap()
    entry["to"] = req.target.lower() if req.target == "functional" else req.target
    entry["send"] = req.send
    entry["response"] = req.response
    entry.fa.set_flow_style()
    return entry


def _entry(
    description: str | None,
    reads: Sequence[str],
    require: str,
    requests: Sequence[ActionRequest],
) -> CommentedMap:
    entry = CommentedMap()
    if description:
        entry["description"] = _folded(description, key="description")
    entry["reads"] = _flow_list(reads)
    entry["require"] = _folded(require, key="require")
    entry["requests"] = CommentedSeq([_request_map(r) for r in requests])
    return entry


def _load_doc(path: Path) -> CommentedMap:
    if not path.exists():
        doc = CommentedMap()
        doc["actions"] = CommentedMap()
        return doc
    data = _yaml().load(path.read_text())
    if data is None:
        data = CommentedMap()
    if not isinstance(data, dict):
        raise ActionError(f"{path.name}: top level must be a mapping")
    if data.get("actions") is None:
        data["actions"] = CommentedMap()
    if not isinstance(data["actions"], dict):
        raise ActionError(f"{path.name}: 'actions:' must be a mapping")
    return data


def _find(actions: dict, name: str) -> str | None:
    for key in actions:
        if str(key).strip().lower() == name:
            return key
    return None


def _safe_write(path: Path, original: str | None, data: CommentedMap) -> None:
    """Dump, then re-parse with the loader's checks; revert on any failure."""
    seq = _detect_seq(original or "") or (4, 2)
    with open(path, "w") as f:
        _dump(data, f, sequence=seq[0], offset=seq[1])
    try:
        parse_actions_doc(yaml_io.safe_load(path.read_text()))
    except Exception as e:
        if original is None:
            path.unlink(missing_ok=True)
        else:
            path.write_text(original)
        if isinstance(e, ActionError):
            raise
        raise ActionError(f"edit failed post-check, reverted: {e}") from e


def add_action(
    name: str,
    *,
    reads: Sequence[str],
    require: str,
    requests: Sequence[ActionRequest],
    description: str | None = None,
    profile: Profile | None = None,
) -> Path:
    """Add a new action (scaffolds actions.yaml when absent). Errors if it exists."""
    key = normalize_name(name)
    entry = _entry(description, reads, require, requests)
    parse_action(key, entry)  # fail before touching the file
    path = _actions_path(profile)
    original = path.read_text() if path.exists() else None
    data = _load_doc(path)
    if _find(data["actions"], key) is not None:
        raise ActionError(f"action {key!r} already exists")
    data["actions"][key] = entry
    _safe_write(path, original, data)
    return path


def update_action(
    name: str,
    *,
    description: str | None = None,
    reads: Sequence[str] | None = None,
    require: str | None = None,
    requests: Sequence[ActionRequest] | None = None,
    profile: Profile | None = None,
) -> Path:
    """Replace the given fields of an existing action. ``description=""`` clears it."""
    key = normalize_name(name)
    path = _actions_path(profile)
    if not path.exists():
        raise ActionError("no actions.yaml to edit")
    original = path.read_text()
    data = _load_doc(path)
    found = _find(data["actions"], key)
    if found is None:
        raise ActionError(f"action {key!r} not found")
    entry = data["actions"][found]
    if not isinstance(entry, dict):
        raise ActionError(f"action {key!r} is not a mapping")
    if description is not None:
        if description:
            entry["description"] = _folded(description, key="description")
        else:
            entry.pop("description", None)
    if reads is not None:
        entry["reads"] = _flow_list(reads)
    if require is not None:
        entry["require"] = _folded(require, key="require")
    if requests is not None:
        entry["requests"] = CommentedSeq([_request_map(r) for r in requests])
    _safe_write(path, original, data)
    return path


def remove_action(name: str, *, profile: Profile | None = None) -> Path:
    """Remove an action. Errors if it isn't declared."""
    key = normalize_name(name)
    path = _actions_path(profile)
    if not path.exists():
        raise ActionError("no actions.yaml to edit")
    original = path.read_text()
    data = _load_doc(path)
    found = _find(data["actions"], key)
    if found is None:
        raise ActionError(f"action {key!r} not found")
    del data["actions"][found]
    _safe_write(path, original, data)
    return path
