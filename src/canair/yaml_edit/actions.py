"""Surgical, comment-preserving edits to a profile's ``actions.yaml``.

Every write re-parses the file with the same checks the loader applies
(:func:`canair.actions.parse_actions_doc`) and reverts on failure, so an edit can
never leave an unguarded or malformed action on disk. Lists are written as inline
flow sequences and each request as a flow mapping, the layout the loader expects
a human to read.
"""

from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path
from typing import TYPE_CHECKING

from ruamel.yaml.comments import CommentedMap, CommentedSeq

from canair.actions import (
    ActionError,
    ActionRequest,
    HeldSession,
    ThenReads,
    normalize_name,
    parse_action,
    parse_actions_doc,
)
from canair.core.yaml_rt import folded as _folded

from .doc import Target, edit_doc
from .ruamel_ops import find_key, flow_map, flow_seq, remove_key

if TYPE_CHECKING:
    from canair.profile.model import Profile


def _request_map(req: ActionRequest) -> CommentedMap:
    return flow_map(
        {
            "to": req.target.lower() if req.target == "functional" else req.target,
            "send": req.send,
            "response": req.response,
        }
    )


def _then_map(then: ThenReads) -> CommentedMap:
    wait: float | int = int(then.wait) if then.wait == int(then.wait) else then.wait
    return flow_map({"wait": wait, "read": flow_seq(then.read)})


def _set_extras(
    entry: CommentedMap,
    hold: Sequence[HeldSession] | None,
    then: ThenReads | None,
    release: Sequence[ActionRequest] | None,
    *,
    clear_then: bool = False,
) -> None:
    """Write ``hold``/``then``/``release`` in the order a reader expects them; an empty list
    (or ``clear_then``) removes the field."""
    if hold is not None:
        if hold:
            entry["hold"] = flow_seq([h.describe() for h in hold])
        else:
            entry.pop("hold", None)
    if clear_then:
        entry.pop("then", None)
    elif then is not None:
        entry["then"] = _then_map(then)
    if release is not None:
        if release:
            entry["release"] = CommentedSeq([_request_map(r) for r in release])
        else:
            entry.pop("release", None)


def _entry(
    description: str | None,
    reads: Sequence[str],
    require: str,
    requests: Sequence[ActionRequest],
    hold: Sequence[HeldSession] = (),
    then: ThenReads | None = None,
    release: Sequence[ActionRequest] = (),
) -> CommentedMap:
    entry = CommentedMap()
    if description:
        entry["description"] = _folded(description, key="description")
    entry["reads"] = flow_seq(reads)
    entry["require"] = _folded(require, key="require")
    if hold:
        entry["hold"] = flow_seq([h.describe() for h in hold])
    entry["requests"] = CommentedSeq([_request_map(r) for r in requests])
    if then is not None:
        entry["then"] = _then_map(then)
    if release:
        entry["release"] = CommentedSeq([_request_map(r) for r in release])
    return entry


def _check(data: object) -> None:
    """Re-parse with the loader's own checks; it raises ``ActionError`` for a bad action."""
    parse_actions_doc(data)


ACTIONS = Target(
    key="actions",
    path=lambda profile: profile.actions_file,
    error=ActionError,
    section="actions",
    check=_check,
)


def _normalized(key: object) -> str:
    return str(key).strip().lower()


def _key(doc, name: str):
    key = find_key(doc.section, name, _normalized)
    if key is None:
        raise ActionError(f"action {name!r} not found")
    return key


def add_action(
    name: str,
    *,
    reads: Sequence[str],
    require: str,
    requests: Sequence[ActionRequest],
    description: str | None = None,
    hold: Sequence[HeldSession] = (),
    then: ThenReads | None = None,
    release: Sequence[ActionRequest] = (),
    profile: Profile,
) -> Path:
    """Add a new action (scaffolds actions.yaml when absent). Errors if it exists."""
    key = normalize_name(name)
    entry = _entry(description, reads, require, requests, hold, then, release)
    parse_action(key, entry)  # fail before touching the file
    with edit_doc(ACTIONS, profile, create=True) as doc:
        if find_key(doc.section, key, _normalized) is not None:
            raise ActionError(f"action {key!r} already exists")
        doc.section[key] = entry
    return doc.path


def update_action(
    name: str,
    *,
    description: str | None = None,
    reads: Sequence[str] | None = None,
    require: str | None = None,
    requests: Sequence[ActionRequest] | None = None,
    hold: Sequence[HeldSession] | None = None,
    then: ThenReads | None = None,
    release: Sequence[ActionRequest] | None = None,
    clear_then: bool = False,
    profile: Profile,
) -> Path:
    """Replace the given fields of an existing action. ``description=""`` clears it, and an
    empty ``hold``/``release`` or ``clear_then`` removes that field."""
    key = normalize_name(name)
    with edit_doc(ACTIONS, profile) as doc:
        entry = doc.section[_key(doc, key)]
        if not isinstance(entry, dict):
            raise ActionError(f"action {key!r} is not a mapping")
        if description is not None:
            if description:
                entry["description"] = _folded(description, key="description")
            else:
                entry.pop("description", None)
        if reads is not None:
            entry["reads"] = flow_seq(reads)
        if require is not None:
            entry["require"] = _folded(require, key="require")
        if requests is not None:
            entry["requests"] = CommentedSeq([_request_map(r) for r in requests])
        _set_extras(entry, hold, then, release, clear_then=clear_then)
    return doc.path


def remove_action(name: str, *, profile: Profile) -> Path:
    """Remove an action. Errors if it isn't declared."""
    key = normalize_name(name)
    with edit_doc(ACTIONS, profile) as doc:
        remove_key(doc.section, _key(doc, key), parent=doc.data, parent_key="actions")
    return doc.path
