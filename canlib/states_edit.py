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


def _flow_implies(implies) -> CommentedSeq | None:
    """Normalize an ``implies:`` value to canonical UPPERCASE, flow-styled.

    Renders inline (``implies: [READY]``) to match how the ``ecus``/``pids``
    editors write ``vehicle_states``. Returns ``None`` for an empty value so the
    caller omits the key.
    """
    toks = parse_implies(implies)
    if not toks:
        return None
    seq = CommentedSeq(list(toks))
    seq.fa.set_flow_style()
    return seq


def _put_implies(entry: CommentedMap, flow: CommentedSeq | None) -> None:
    """Set/clear ``implies:`` on an existing entry, keeping it above ``when:``.

    Appending would put the key *after* the last key's trailing comment — which,
    for the final entry, is the block comment introducing the next state. Insert
    at ``when:``'s position instead so the canonical field order
    (name/description/implies/when) holds however the entry was authored.
    """
    if flow is None:
        entry.pop("implies", None)
        return
    if "implies" in entry or "when" not in entry:
        entry["implies"] = flow
        return
    entry.insert(list(entry).index("when"), "implies", flow)


def _new_entry(name: str, description: str | None, when: str | None, implies=None) -> CommentedMap:

    entry = CommentedMap()
    entry["name"] = name
    if description:
        entry["description"] = description
    flow = _flow_implies(implies)
    if flow is not None:
        entry["implies"] = flow
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
    implies: dict[str, tuple[str, ...]] = {}
    for entry in states:
        if not isinstance(entry, dict) or "name" not in entry:
            raise StatesEditError("each state needs a 'name' after edit")
        nm = str(entry["name"]).upper()
        if nm in seen:
            raise StatesEditError(f"duplicate state name {nm!r} after edit")
        seen.add(nm)
        implies[nm] = parse_implies(entry.get("implies"))
        when = entry.get("when")
        if when:
            compile_predicate(str(when))  # raises StatePredicateError on bad syntax
    _validate_implies(implies, seen)


def _validate_implies(implies: dict[str, tuple[str, ...]], declared: set[str]) -> None:
    """Check the ``implies:`` hierarchy is well-formed and acyclic after an edit.

    Mirrors ``canair validate states`` so a bad hierarchy is reverted at write
    time rather than shipped and only caught later.
    """
    for name, targets in implies.items():
        for target in targets:
            if target == name:
                raise StatesEditError(f"state {name!r} implies itself after edit")
            if target == ALL_STATE:
                raise StatesEditError(f"state {name!r} implies ALL, the meta-token")
            if target not in declared:
                raise StatesEditError(f"state {name!r} implies undeclared state {target!r}")
        if name == ALL_STATE and targets:
            raise StatesEditError("the ALL meta-token cannot imply anything")
    cycle = implication_cycle([StateRule(name=n, implies=t) for n, t in implies.items()])
    if cycle:
        raise StatesEditError(f"implies: cycle {' -> '.join(cycle)} after edit")


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
    profile: Profile | None = None,
) -> Path:
    """Append a new state to the vocabulary (scaffolds the file if absent).

    Rejects a duplicate name (case-insensitive), an invalid ``when:`` predicate
    and a malformed/cyclic ``implies:`` hierarchy. The write is verified by a
    re-parse; on failure it is reverted.
    """
    name = normalize_name(name)
    if when:
        compile_predicate(when)  # fail fast on bad syntax, before touching disk

    path = _states_path(profile)
    original = path.read_text() if path.exists() else None
    data = _load_doc(path)
    if _find(data["states"], name) != -1:
        raise StatesEditError(f"state {name!r} already exists")
    data["states"].append(_new_entry(name, description, when, implies))
    _safe_write(path, original, data)
    return path


def remove_state(name: str, *, profile: Profile | None = None) -> Path:
    """Remove a state from the vocabulary. Errors if it isn't declared.

    The removed entry's own leading comment goes with it; every surviving
    entry keeps the comment written above it (see :func:`_delete_state_entry`).
    Sibling ``implies:`` targets naming it are dropped, so the hierarchy is
    never left pointing at a state that no longer exists.
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
        targets = parse_implies(entry.get("implies"))
        if name in targets:
            _put_implies(entry, _flow_implies([t for t in targets if t != name]))
    _safe_write(path, original, data)
    return path


def rename_state(old: str, new: str, *, profile: Profile | None = None) -> Path:
    """Rename a state's ``name`` in the vocabulary (references are NOT rewritten).

    Only the vocabulary entry changes; existing ECU/capture ``vehicle_states``
    references keep the old token. The caller should warn the user to update
    those (or re-run the migration) — this keeps the edit surgical.

    Sibling ``implies:`` targets ARE retargeted: they live in this same file, so
    leaving one dangling would make the rename fail its own post-write check.
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
        targets = parse_implies(entry.get("implies"))
        if old in targets:
            entry["implies"] = _flow_implies([new if t == old else t for t in targets])  # in place
    _safe_write(path, original, data)
    return path


def set_state_field(
    name: str,
    field: str,
    value: str | None,
    *,
    profile: Profile | None = None,
) -> Path:
    """Set/clear a state's ``description``, ``when:`` predicate or ``implies:``.

    A ``None``/empty ``value`` removes the field. A ``when:`` value is
    syntax-checked before the write; an ``implies:`` value is a comma-separated
    list of state names, written as an inline flow list and checked for
    undeclared targets and cycles by the post-write re-parse.
    """
    if field not in ("description", "when", "implies"):
        raise StatesEditError("field must be 'description', 'when' or 'implies'")
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
    if field == "implies":
        _put_implies(entry, _flow_implies(value))
    elif value:
        entry[field] = value
    elif field in entry:
        del entry[field]
    _safe_write(path, original, data)
    return path
