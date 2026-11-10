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
    FrameRequest,
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


def _spaced_hex(data: bytes) -> str:
    """8 bytes as ``00 10 ...`` — the readable layout the loader tolerates."""
    return " ".join(f"{b:02X}" for b in data)


def _frame_map(frame: FrameRequest) -> CommentedMap:
    entry = CommentedMap()
    entry["id"] = f"0x{frame.can_id:X}"
    entry["data"] = _spaced_hex(frame.data)
    entry["mask"] = _spaced_hex(frame.mask)
    if frame.counter is not None:
        counter = {"byte": frame.counter.byte, "nibble": "high" if frame.counter.high else "low"}
        if frame.counter.step != 1:
            counter["step"] = frame.counter.step
        entry["counter"] = flow_map(counter)
    if frame.rate_ms is not None:
        entry["rate_ms"] = frame.rate_ms
        if frame.count is not None:
            entry["count"] = frame.count
        if frame.duration_s is not None:
            whole = frame.duration_s == int(frame.duration_s)
            entry["duration_s"] = int(frame.duration_s) if whole else frame.duration_s
    if frame.bus is not None:
        entry["bus"] = frame.bus
    return entry


def _then_map(then: ThenReads) -> CommentedMap:
    wait: float | int = int(then.wait) if then.wait == int(then.wait) else then.wait
    return flow_map({"wait": wait, "read": flow_seq(then.read)})


def _set_extras(
    entry: CommentedMap,
    hold: Sequence[HeldSession] | None,
    then: ThenReads | None,
    release: Sequence[ActionRequest] | None,
    frames: Sequence[FrameRequest] | None = None,
    *,
    clear_then: bool = False,
) -> None:
    """Write ``hold``/``then``/``release``/``frames`` in the order a reader expects them; an
    empty list (or ``clear_then``) removes the field."""
    if hold is not None:
        if hold:
            entry["hold"] = flow_seq([h.describe() for h in hold])
        else:
            entry.pop("hold", None)
    if frames is not None:
        if frames:
            entry["frames"] = CommentedSeq([_frame_map(f) for f in frames])
        else:
            entry.pop("frames", None)
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
    frames: Sequence[FrameRequest] = (),
) -> CommentedMap:
    entry = CommentedMap()
    if description:
        entry["description"] = _folded(description, key="description")
    entry["reads"] = flow_seq(reads)
    entry["require"] = _folded(require, key="require")
    if hold:
        entry["hold"] = flow_seq([h.describe() for h in hold])
    if requests:
        entry["requests"] = CommentedSeq([_request_map(r) for r in requests])
    if frames:
        entry["frames"] = CommentedSeq([_frame_map(f) for f in frames])
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
    frames: Sequence[FrameRequest] = (),
    profile: Profile,
) -> Path:
    """Add a new action (scaffolds actions.yaml when absent). Errors if it exists."""
    key = normalize_name(name)
    entry = _entry(description, reads, require, requests, hold, then, release, frames)
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
    frames: Sequence[FrameRequest] | None = None,
    clear_then: bool = False,
    profile: Profile,
) -> Path:
    """Replace the given fields of an existing action. ``description=""`` clears it, and an
    empty ``hold``/``release``/``frames`` or ``clear_then`` removes that field."""
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
            if requests:
                entry["requests"] = CommentedSeq([_request_map(r) for r in requests])
            else:
                entry.pop("requests", None)
        _set_extras(entry, hold, then, release, frames, clear_then=clear_then)
    return doc.path


def remove_action(name: str, *, profile: Profile) -> Path:
    """Remove an action. Errors if it isn't declared."""
    key = normalize_name(name)
    with edit_doc(ACTIONS, profile) as doc:
        remove_key(doc.section, _key(doc, key), parent=doc.data, parent_key="actions")
    return doc.path
