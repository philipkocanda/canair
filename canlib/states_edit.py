"""Surgical, comment-preserving edits to a profile's ``vehicle_states.yaml``.

The vehicle-state vocabulary (``profiles/<name>/vehicle_states.yaml``) is a
hand-curated, ordered list of the car's power/operating states — the analogue
of ``can_buses.yaml`` for the state axis. These helpers let ``canair states``
add/remove/rename a state and edit its description or ``when:`` predicate
without clobbering the file's comments or layout.

Every write goes through :func:`_safe_write`, which re-parses and re-validates
the file (structure + predicate syntax) and reverts on failure — a broken edit
never lands on disk. State names are normalized to the canonical UPPERCASE form
(see :mod:`canlib.states`).
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import TYPE_CHECKING

from ruamel.yaml.comments import CommentedMap, CommentedSeq

from . import yaml_io
from .states import StatePredicateError, compile_predicate
from .yaml_rt import detect_sequence_indent as _detect_seq
from .yaml_rt import dump as _dump
from .yaml_rt import round_trip_yaml as _yaml

if TYPE_CHECKING:
    from .profile import Profile

# A state name: UPPERCASE letters/digits, single-space-separated words
# (e.g. ``READY``, ``DEEP SLEEP``). Kept deliberately narrow so a name stays a
# clean flow-list token.
_NAME_RE = re.compile(r"^[A-Z0-9]+(?: [A-Z0-9]+)*$")


class StatesEditError(Exception):
    """Raised when a vehicle_states.yaml edit cannot be applied safely."""


def _states_path(profile: Profile | None = None) -> Path:
    from .profile import active

    return (profile or active()).states_file


def normalize_name(name: str) -> str:
    """Normalize + validate a state name to the canonical UPPERCASE form."""
    norm = " ".join(str(name).strip().upper().split())
    if not norm:
        raise StatesEditError("state name must not be empty")
    if not _NAME_RE.match(norm):
        raise StatesEditError(
            f"invalid state name {name!r} — use UPPERCASE letters/digits "
            "(spaces allowed, e.g. 'DEEP SLEEP')"
        )
    return norm


def _load_doc(path: Path) -> CommentedMap:
    """Load the states file round-trip; scaffold an empty doc when absent."""
    if not path.exists():
        doc = CommentedMap()
        doc["states"] = CommentedSeq()
        return doc
    data = _yaml().load(path.read_text())
    if data is None:
        data = CommentedMap()
    if not isinstance(data, dict):
        raise StatesEditError(f"{path.name}: top-level must be a mapping")
    if "states" not in data or data["states"] is None:
        data["states"] = CommentedSeq()
    if not isinstance(data["states"], list):
        raise StatesEditError(f"{path.name}: 'states:' must be a list")
    return data


def _find(states: list, name: str) -> int:
    """Index of the state named ``name`` (case-insensitive), or -1."""
    for i, entry in enumerate(states):
        if isinstance(entry, dict) and str(entry.get("name", "")).upper() == name:
            return i
    return -1


def _new_entry(name: str, description: str | None, when: str | None) -> CommentedMap:
    entry = CommentedMap()
    entry["name"] = name
    if description:
        entry["description"] = description
    if when:
        entry["when"] = when
    return entry


def _reparse_validate(path: Path) -> None:
    """Re-parse the written file and validate structure + predicate syntax."""
    data = yaml_io.safe_load(path.read_text()) or {}
    states = data.get("states")
    if not isinstance(states, list):
        raise StatesEditError("top-level 'states:' must be a list after edit")
    seen: set[str] = set()
    for entry in states:
        if not isinstance(entry, dict) or "name" not in entry:
            raise StatesEditError("each state needs a 'name' after edit")
        nm = str(entry["name"]).upper()
        if nm in seen:
            raise StatesEditError(f"duplicate state name {nm!r} after edit")
        seen.add(nm)
        when = entry.get("when")
        if when:
            compile_predicate(str(when))  # raises StatePredicateError on bad syntax


def _safe_write(path: Path, original: str | None, data) -> None:
    """Dump ``data``, then re-parse + re-validate; revert on any failure."""
    seq = _detect_seq(original or "") or (4, 2)
    with open(path, "w") as f:
        _dump(data, f, sequence=seq[0], offset=seq[1])
    try:
        _reparse_validate(path)
    except (StatesEditError, StatePredicateError):
        _restore(path, original)
        raise
    except Exception as e:  # pragma: no cover - defensive
        _restore(path, original)
        raise StatesEditError(f"edit failed post-check, reverted: {e}") from e


def _restore(path: Path, original: str | None) -> None:
    if original is None:
        path.unlink(missing_ok=True)
    else:
        path.write_text(original)


# ── public editors ──────────────────────────────────────────────────────────


def add_state(
    name: str,
    *,
    description: str | None = None,
    when: str | None = None,
    profile: Profile | None = None,
) -> Path:
    """Append a new state to the vocabulary (scaffolds the file if absent).

    Rejects a duplicate name (case-insensitive) and an invalid ``when:``
    predicate. The write is verified by a re-parse; on failure it is reverted.
    """
    name = normalize_name(name)
    if when:
        compile_predicate(when)  # fail fast on bad syntax, before touching disk

    path = _states_path(profile)
    original = path.read_text() if path.exists() else None
    data = _load_doc(path)
    if _find(data["states"], name) != -1:
        raise StatesEditError(f"state {name!r} already exists")
    data["states"].append(_new_entry(name, description, when))
    _safe_write(path, original, data)
    return path


def remove_state(name: str, *, profile: Profile | None = None) -> Path:
    """Remove a state from the vocabulary. Errors if it isn't declared."""
    name = normalize_name(name)
    path = _states_path(profile)
    if not path.exists():
        raise StatesEditError("no vehicle_states.yaml to edit")
    original = path.read_text()
    data = _load_doc(path)
    idx = _find(data["states"], name)
    if idx == -1:
        raise StatesEditError(f"state {name!r} not found")
    del data["states"][idx]
    _safe_write(path, original, data)
    return path


def rename_state(old: str, new: str, *, profile: Profile | None = None) -> Path:
    """Rename a state's ``name`` in the vocabulary (references are NOT rewritten).

    Only the vocabulary entry changes; existing ECU/capture ``vehicle_states``
    references keep the old token. The caller should warn the user to update
    those (or re-run the migration) — this keeps the edit surgical.
    """
    old = normalize_name(old)
    new = normalize_name(new)
    path = _states_path(profile)
    if not path.exists():
        raise StatesEditError("no vehicle_states.yaml to edit")
    original = path.read_text()
    data = _load_doc(path)
    idx = _find(data["states"], old)
    if idx == -1:
        raise StatesEditError(f"state {old!r} not found")
    if old != new and _find(data["states"], new) != -1:
        raise StatesEditError(f"state {new!r} already exists")
    data["states"][idx]["name"] = new
    _safe_write(path, original, data)
    return path


def set_state_field(
    name: str,
    field: str,
    value: str | None,
    *,
    profile: Profile | None = None,
) -> Path:
    """Set/clear a state's ``description`` or ``when:`` predicate.

    A ``None``/empty ``value`` removes the field. A ``when:`` value is
    syntax-checked before the write. The write is verified by a re-parse.
    """
    if field not in ("description", "when"):
        raise StatesEditError("field must be 'description' or 'when'")
    name = normalize_name(name)
    if field == "when" and value:
        compile_predicate(value)

    path = _states_path(profile)
    if not path.exists():
        raise StatesEditError("no vehicle_states.yaml to edit")
    original = path.read_text()
    data = _load_doc(path)
    idx = _find(data["states"], name)
    if idx == -1:
        raise StatesEditError(f"state {name!r} not found")
    entry = data["states"][idx]
    if value:
        entry[field] = value
    elif field in entry:
        del entry[field]
    _safe_write(path, original, data)
    return path
