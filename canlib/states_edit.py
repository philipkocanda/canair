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
from .states import (
    ALL_STATE,
    StatePredicateError,
    StateRule,
    compile_predicate,
    exclusion_conflicts,
    implication_cycle,
    parse_implies,
)
from .yaml_rt import detect_sequence_indent as _detect_seq
from .yaml_rt import dump as _dump
from .yaml_rt import round_trip_yaml as _yaml

if TYPE_CHECKING:
    from .profile import Profile

# A state name: a single UPPERCASE alphanumeric word (e.g. ``READY``, ``ACC2``,
# ``DEEPSLEEP``). No spaces/underscores/punctuation — a controlled-vocabulary
# token, kept clean for the inline flow lists that reference it.
_NAME_RE = re.compile(r"^[A-Z0-9]+$")


class StatesEditError(Exception):
    """Raised when a vehicle_states.yaml edit cannot be applied safely."""


def _states_path(profile: Profile | None = None) -> Path:
    from .profile import active

    return (profile or active()).states_file


def normalize_name(name: str) -> str:
    """Normalize + validate a state name to the canonical UPPERCASE form."""
    norm = str(name).strip().upper()
    if not norm:
        raise StatesEditError("state name must not be empty")
    if not _NAME_RE.match(norm):
        raise StatesEditError(
            f"invalid state name {name!r} — a state is a single UPPERCASE "
            "alphanumeric word (letters/digits only, no spaces or punctuation)"
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


# Canonical field order within a state entry. Both relation keys sit between the
# prose and the predicate, so a new one is inserted at the first key that follows
# it rather than appended (see _put_relation).
_RELATION_FIELDS = ("implies", "excludes")
_FIELD_ORDER = ("name", "description", "implies", "excludes", "when")


def _flow_states(value) -> CommentedSeq | None:
    """Normalize a relation value (``implies:``/``excludes:``) to flow-styled UPPERCASE.

    Renders inline (``implies: [READY]``) to match how the ``ecus``/``pids``
    editors write ``vehicle_states``. Returns ``None`` for an empty value so the
    caller omits the key.
    """
    toks = parse_implies(value)
    if not toks:
        return None
    seq = CommentedSeq(list(toks))
    seq.fa.set_flow_style()
    return seq


def _put_relation(entry: CommentedMap, field: str, flow: CommentedSeq | None) -> None:
    """Set/clear a relation key on an existing entry, keeping canonical field order.

    Appending would put the key *after* the last key's trailing comment — which,
    for the final entry, is the block comment introducing the next state. Insert
    at the position of the first key that should follow it instead, so the
    canonical order (name/description/implies/excludes/when) holds however the
    entry was authored.
    """
    if flow is None:
        entry.pop(field, None)
        return
    if field in entry:
        entry[field] = flow
        return
    successors = _FIELD_ORDER[_FIELD_ORDER.index(field) + 1 :]
    present = [k for k in successors if k in entry]
    if not present:
        entry[field] = flow
        return
    entry.insert(list(entry).index(present[0]), field, flow)


def _new_entry(
    name: str,
    description: str | None,
    when: str | None,
    implies=None,
    excludes=None,
) -> CommentedMap:
    entry = CommentedMap()
    entry["name"] = name
    if description:
        entry["description"] = description
    for field, value in (("implies", implies), ("excludes", excludes)):
        flow = _flow_states(value)
        if flow is not None:
            entry[field] = flow
    if when:
        entry["when"] = when
    return entry


def _comment_slot(data: CommentedMap, idx: int) -> tuple[dict, object | None, int]:
    """Locate where the comment block *preceding* ``states[idx]`` is stored.

    ruamel does not keep a sequence item's leading comment on the item itself:
    it lands as the trailing comment of the *previous* item's last key, or — for
    the first item — in the pre-comment list of the ``states`` key. Returns the
    owning ``ca.items`` dict, the key within it, and the slot index.
    """
    if idx <= 0:
        return data.ca.items, "states", 3
    prev = data["states"][idx - 1]
    keys = list(prev.keys()) if isinstance(prev, dict) else []
    return prev.ca.items, (keys[-1] if keys else None), 2


def _as_pre_comment(token) -> list:
    """Split a trailing CommentToken into per-line tokens for a pre-comment list.

    A trailing comment stores the whole block (blank lines and indentation
    included) in one token, whereas a pre-comment list holds one token per line.
    """
    from ruamel.yaml.error import CommentMark
    from ruamel.yaml.tokens import CommentToken

    column = getattr(getattr(token, "start_mark", None), "column", 2)
    lines = [ln.strip() for ln in str(token.value).splitlines()]
    return [CommentToken(f"{ln}\n", CommentMark(column)) for ln in lines if ln]


def _delete_state_entry(data: CommentedMap, idx: int) -> None:
    """Delete ``states[idx]`` without stealing the next entry's leading comment.

    A plain ``del`` loses the *following* entry's comment (ruamel stores it on
    the victim) and orphans the victim's own comment onto its successor — so
    removing a state silently re-labels the next one. Re-homing the victim's
    trailing comment onto the slot that held its leading comment keeps every
    surviving entry with the comment written above it.
    """
    v_items, v_key, v_slot = _comment_slot(data, idx + 1)
    victim_trailing = v_items.get(v_key, [None] * 4)[v_slot] if v_key is not None else None

    d_items, d_key, d_slot = _comment_slot(data, idx)
    del data["states"][idx]
    if d_key is None:
        return
    slot = d_items.setdefault(d_key, [None, None, None, None])
    if victim_trailing is None:
        slot[d_slot] = None
    elif d_slot == 3:  # the parent key's pre-comment list wants per-line tokens
        slot[d_slot] = _as_pre_comment(victim_trailing)
    else:
        slot[d_slot] = victim_trailing


def _reparse_validate(path: Path) -> None:
    """Re-parse the written file and validate structure + predicate syntax."""
    data = yaml_io.safe_load(path.read_text()) or {}
    states = data.get("states")
    if not isinstance(states, list):
        raise StatesEditError("top-level 'states:' must be a list after edit")
    seen: set[str] = set()
    relations: dict[str, dict[str, tuple[str, ...]]] = {f: {} for f in _RELATION_FIELDS}
    for entry in states:
        if not isinstance(entry, dict) or "name" not in entry:
            raise StatesEditError("each state needs a 'name' after edit")
        nm = str(entry["name"]).upper()
        if nm in seen:
            raise StatesEditError(f"duplicate state name {nm!r} after edit")
        seen.add(nm)
        for field in _RELATION_FIELDS:
            relations[field][nm] = parse_implies(entry.get(field))
        when = entry.get("when")
        if when:
            compile_predicate(str(when))  # raises StatePredicateError on bad syntax
    _validate_relations(relations, seen)


def _validate_relations(
    relations: dict[str, dict[str, tuple[str, ...]]], declared: set[str]
) -> None:
    """Check ``implies:``/``excludes:`` are well-formed after an edit.

    Mirrors ``canair validate states`` so a bad relation is reverted at write
    time rather than shipped and only caught later.
    """
    for field, verb, infinitive in (
        ("implies", "implies", "imply"),
        ("excludes", "excludes", "exclude"),
    ):
        for name, targets in relations[field].items():
            for target in targets:
                if target == name:
                    raise StatesEditError(f"state {name!r} {verb} itself after edit")
                if target == ALL_STATE:
                    raise StatesEditError(f"state {name!r} {verb} ALL, the meta-token")
                if target not in declared:
                    raise StatesEditError(f"state {name!r} {verb} undeclared state {target!r}")
            if name == ALL_STATE and targets:
                raise StatesEditError(f"the ALL meta-token cannot {infinitive} anything")
    rules = [
        StateRule(
            name=n,
            implies=relations["implies"].get(n, ()),
            excludes=relations["excludes"].get(n, ()),
        )
        for n in declared
    ]
    cycle = implication_cycle(rules)
    if cycle:
        raise StatesEditError(f"implies: cycle {' -> '.join(cycle)} after edit")
    conflicts = exclusion_conflicts(rules)
    if conflicts:
        pairs = ", ".join(f"{a}+{b}" for a, b in conflicts)
        raise StatesEditError(
            f"excludes: contradicts implies: for {pairs} after edit — "
            "implies: says one always holds with the other, excludes: says it never can"
        )


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
    implies=None,
    excludes=None,
    profile: Profile | None = None,
) -> Path:
    """Append a new state to the vocabulary (scaffolds the file if absent).

    Rejects a duplicate name (case-insensitive), an invalid ``when:`` predicate,
    a malformed/cyclic ``implies:`` hierarchy and an ``excludes:`` that
    contradicts it. The write is verified by a re-parse; on failure it is
    reverted.
    """
    name = normalize_name(name)
    if when:
        compile_predicate(when)  # fail fast on bad syntax, before touching disk

    path = _states_path(profile)
    original = path.read_text() if path.exists() else None
    data = _load_doc(path)
    if _find(data["states"], name) != -1:
        raise StatesEditError(f"state {name!r} already exists")
    data["states"].append(_new_entry(name, description, when, implies, excludes))
    _safe_write(path, original, data)
    return path


def remove_state(name: str, *, profile: Profile | None = None) -> Path:
    """Remove a state from the vocabulary. Errors if it isn't declared.

    The removed entry's own leading comment goes with it; every surviving
    entry keeps the comment written above it (see :func:`_delete_state_entry`).
    Sibling ``implies:``/``excludes:`` targets naming it are dropped, so neither
    relation is left pointing at a state that no longer exists.
    """
    name = normalize_name(name)
    path = _states_path(profile)
    if not path.exists():
        raise StatesEditError("no vehicle_states.yaml to edit")
    original = path.read_text()
    data = _load_doc(path)
    idx = _find(data["states"], name)
    if idx == -1:
        raise StatesEditError(f"state {name!r} not found")
    _delete_state_entry(data, idx)
    for entry in data["states"]:
        for field in _RELATION_FIELDS:
            targets = parse_implies(entry.get(field))
            if name in targets:
                _put_relation(entry, field, _flow_states([t for t in targets if t != name]))
    _safe_write(path, original, data)
    return path


def rename_state(old: str, new: str, *, profile: Profile | None = None) -> Path:
    """Rename a state's ``name`` in the vocabulary (references are NOT rewritten).

    Only the vocabulary entry changes; existing ECU/capture ``vehicle_states``
    references keep the old token. The caller should warn the user to update
    those (or re-run the migration) — this keeps the edit surgical.

    Sibling ``implies:``/``excludes:`` targets ARE retargeted: they live in this
    same file, so leaving one dangling would make the rename fail its own
    post-write check.
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
    for entry in data["states"]:
        for field in _RELATION_FIELDS:
            targets = parse_implies(entry.get(field))
            if old in targets:
                # In place: the key already exists, so field order is unaffected.
                entry[field] = _flow_states([new if t == old else t for t in targets])
    _safe_write(path, original, data)
    return path


def set_state_field(
    name: str,
    field: str,
    value: str | None,
    *,
    profile: Profile | None = None,
) -> Path:
    """Set/clear a state's ``description``, ``when:``, ``implies:`` or ``excludes:``.

    A ``None``/empty ``value`` removes the field. A ``when:`` value is
    syntax-checked before the write; a relation value is a comma-separated list
    of state names, written as an inline flow list and checked for undeclared
    targets, cycles and implies/excludes contradictions by the post-write
    re-parse.
    """
    if field not in ("description", "when", *_RELATION_FIELDS):
        allowed = "', '".join(("description", "when", *_RELATION_FIELDS))
        raise StatesEditError(f"field must be one of '{allowed}'")
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
    if field in _RELATION_FIELDS:
        _put_relation(entry, field, _flow_states(value))
    elif value:
        entry[field] = value
    elif field in entry:
        del entry[field]
    _safe_write(path, original, data)
    return path
