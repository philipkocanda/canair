"""Surgical, comment-preserving edits to a profile's ``vehicle_states.yaml``.

The vehicle-state vocabulary (``profiles/<name>/vehicle_states.yaml``) is a
hand-curated, ordered list of the car's power/operating states — the analogue
of ``can_buses.yaml`` for the state axis. These helpers let ``canair states``
add/remove/rename a state and edit its description or ``when:`` predicate
without clobbering the file's comments or layout.

Every write goes through :func:`canair.yaml_edit.doc.edit_doc`, which re-parses and re-validates
the file (structure + predicate syntax) and reverts on failure — a broken edit
never lands on disk. State names are normalized to the canonical UPPERCASE form
(see :mod:`canair.states`).
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import TYPE_CHECKING

from ruamel.yaml.comments import CommentedMap, CommentedSeq

from canair.core.edit_errors import EditError
from canair.core.paths import schema_modeline
from canair.states.implies import exclusion_conflicts, implication_cycle, parse_implies
from canair.states.predicates import StateRule, compile_predicate
from canair.states.tokens import ALL_STATE, unquoted_boolean_error

from .doc import Target, edit_doc
from .ruamel_ops import insert_item_before_last, place_in_order, remove_item
from .ruamel_ops import state_token as _token

if TYPE_CHECKING:
    from canair.profile.model import Profile

# A state name: a single UPPERCASE alphanumeric word (e.g. ``READY``, ``ACC2``,
# ``DEEPSLEEP``). No spaces/underscores/punctuation — a controlled-vocabulary
# token, kept clean for the inline flow lists that reference it.
_NAME_RE = re.compile(r"^[A-Z0-9]+$")


class StatesEditError(EditError):
    """Raised when a vehicle_states.yaml edit cannot be applied safely."""


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


def find_state(states: list, name: str) -> int:
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
    seq = CommentedSeq([_token(t) for t in toks])
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
    place_in_order(entry, field, flow, _FIELD_ORDER)


def _new_entry(
    name: str,
    description: str | None,
    when: str | None,
    implies=None,
    excludes=None,
) -> CommentedMap:
    entry = CommentedMap()
    entry["name"] = _token(name)
    if description:
        entry["description"] = description
    for field, value in (("implies", implies), ("excludes", excludes)):
        flow = _flow_states(value)
        if flow is not None:
            entry[field] = flow
    if when:
        entry["when"] = when
    return entry


def _check(data: dict | None) -> None:
    """Validate the re-parsed file: structure and predicate syntax."""
    states = (data or {}).get("states")
    if not isinstance(states, list):
        raise StatesEditError("top-level 'states:' must be a list after edit")
    seen: set[str] = set()
    relations: dict[str, dict[str, tuple[str, ...]]] = {f: {} for f in _RELATION_FIELDS}
    for entry in states:
        if not isinstance(entry, dict) or "name" not in entry:
            raise StatesEditError("each state needs a 'name' after edit")
        for key in ("name", *_RELATION_FIELDS):
            if msg := unquoted_boolean_error(f"state {key}", entry.get(key)):
                raise StatesEditError(msg)
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


STATES = Target(
    key="states",
    path=lambda profile: profile.states_file,
    error=StatesEditError,
    section="states",
    kind=CommentedSeq,
    check=_check,
    header=schema_modeline("vehicle_states") + "\n",
)


# ── public editors ──────────────────────────────────────────────────────────


def add_state(
    name: str,
    *,
    description: str | None = None,
    when: str | None = None,
    implies=None,
    excludes=None,
    profile: Profile,
) -> Path:
    """Add a new state to the vocabulary (scaffolds the file if absent).

    The state is appended, or inserted just before a trailing ``ALL`` entry so the
    catch-all stays last. Rejects a duplicate name (case-insensitive), an invalid
    ``when:`` predicate, a malformed/cyclic ``implies:`` hierarchy and an
    ``excludes:`` that contradicts it. The write is verified by a re-parse; on
    failure it is reverted.
    """
    name = normalize_name(name)
    if when:
        compile_predicate(when)  # fail fast on bad syntax, before touching disk

    with edit_doc(STATES, profile, create=True) as doc:
        states = doc.section
        if find_state(states, name) != -1:
            raise StatesEditError(f"state {name!r} already exists")
        entry = _new_entry(name, description, when, implies, excludes)
        # ALL is the catch-all, conventionally listed last: a new state goes before it,
        # and the comment written above ALL stays above it.
        last = states[-1] if len(states) else None
        if isinstance(last, dict) and str(last.get("name", "")).upper() == ALL_STATE:
            insert_item_before_last(doc.data, "states", entry)
        else:
            states.append(entry)
    return doc.path


def remove_state(name: str, *, profile: Profile) -> Path:
    """Remove a state from the vocabulary. Errors if it isn't declared.

    The removed entry's own leading comment goes with it; every surviving
    entry keeps the comment written above it (see :func:`ruamel_ops.remove_item`).
    Sibling ``implies:``/``excludes:`` targets naming it are dropped, so neither
    relation is left pointing at a state that no longer exists.
    """
    name = normalize_name(name)
    with edit_doc(STATES, profile) as doc:
        idx = find_state(doc.section, name)
        if idx == -1:
            raise StatesEditError(f"state {name!r} not found")
        remove_item(doc.section, idx, parent=doc.data, parent_key="states")
        for entry in doc.section:
            for field in _RELATION_FIELDS:
                targets = parse_implies(entry.get(field))
                if name in targets:
                    _put_relation(entry, field, _flow_states([t for t in targets if t != name]))
    return doc.path


def rename_state(old: str, new: str, *, profile: Profile) -> Path:
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
    with edit_doc(STATES, profile) as doc:
        idx = find_state(doc.section, old)
        if idx == -1:
            raise StatesEditError(f"state {old!r} not found")
        if old != new and find_state(doc.section, new) != -1:
            raise StatesEditError(f"state {new!r} already exists")
        doc.section[idx]["name"] = _token(new)
        for entry in doc.section:
            for field in _RELATION_FIELDS:
                targets = parse_implies(entry.get(field))
                if old in targets:
                    # In place: the key already exists, so field order is unaffected.
                    entry[field] = _flow_states([new if t == old else t for t in targets])
    return doc.path


def set_state_field(
    name: str,
    field: str,
    value: str | None,
    *,
    profile: Profile,
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

    with edit_doc(STATES, profile) as doc:
        idx = find_state(doc.section, name)
        if idx == -1:
            raise StatesEditError(f"state {name!r} not found")
        entry = doc.section[idx]
        if field in _RELATION_FIELDS:
            _put_relation(entry, field, _flow_states(value))
        elif value:
            entry[field] = value
        elif field in entry:
            del entry[field]
    return doc.path
