"""Surgical, comment-preserving edits to a profile's ``variants.yaml``.

``canair variants`` adds, renames and removes variants and sets their metadata through
these helpers, so the file is never hand-edited. Every write is re-parsed and checked
with :func:`canlib.variants.check_variants_doc` and reverted on failure, so a broken
edit never lands on disk. Values arrive as command-line text and are typed here, per
field, from the same field table the checker uses.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import TYPE_CHECKING, Any

from ruamel.yaml.comments import CommentedMap, CommentedSeq
from ruamel.yaml.scalarstring import DoubleQuotedScalarString

from canlib import yaml_io
from canlib.variants import (
    BATTERY_FIELDS,
    VARIANT_FIELDS,
    check_variants_doc,
    id_problem,
    normalize_variant_id,
)
from canlib.yaml_rt import NOTE_WRAP_WIDTH, folded
from canlib.yaml_rt import dump as _dump
from canlib.yaml_rt import round_trip_yaml as _yaml

if TYPE_CHECKING:
    from canlib.profile import Profile

_HEADER = (
    "# The variants of this car (battery, facelift, market) and their metadata.\n"
    "# List or edit with `canair variants`. See docs/concepts/variants.md.\n"
)


class VariantsEditError(Exception):
    """Raised when a ``variants.yaml`` edit cannot be applied safely."""


def _path(profile: Profile | None) -> Path:
    from canlib.profile import active

    return (profile or active()).variants_file


def _checked_id(raw: str) -> str:
    vid = normalize_variant_id(raw)
    if problem := id_problem(vid):
        raise VariantsEditError(problem)
    return vid


# Where a field sits: ``  <id>:`` then ``    <field>:``.
_FIELD_INDENT = 4


def _flow_seq(items: Sequence[Any]) -> CommentedSeq:
    seq = CommentedSeq(items)
    seq.fa.set_flow_style()
    return seq


def _text_list(key: str, items: list[str]) -> CommentedSeq:
    """A list of text, inline when it fits on the field's line, else one item per line."""
    inline = f"{' ' * _FIELD_INDENT}{key}: [{', '.join(items)}]"
    if len(inline) <= NOTE_WRAP_WIDTH:
        seq = CommentedSeq(items)
        seq.fa.set_flow_style()
    else:
        # A long item wraps like a note: `    - ` then the text.
        seq = CommentedSeq([folded(i, key_indent=_FIELD_INDENT, key="-") for i in items])
        seq.fa.set_block_style()
    return seq


def _number(key: str, raw: str) -> int | float:
    try:
        value = float(raw)
    except ValueError:
        raise VariantsEditError(f"{key}: {raw!r} is not a number") from None
    return int(value) if value.is_integer() and "." not in raw else value


def _pair(key: str, raw: str) -> list[str]:
    parts = [p.strip() for p in raw.split(",")]
    if len(parts) != 2 or not all(parts):
        raise VariantsEditError(f"{key}: give two comma-separated values, e.g. 2016,2019-05")
    return parts


def parse_value(key: str, raw: str) -> Any:
    """Type a command-line ``raw`` value for field ``key`` (``battery.<x>`` for battery)."""
    if key.startswith("battery."):
        sub = key.split(".", 1)[1]
        kind = BATTERY_FIELDS.get(sub)
        if kind is None:
            raise VariantsEditError(
                f"unknown battery field '{sub}' (known: {', '.join(BATTERY_FIELDS)})"
            )
        if kind == "text":
            return raw.strip()
        value = _number(key, raw)
        if kind == "count" and not isinstance(value, int):
            raise VariantsEditError(f"{key}: must be a whole number")
        return value
    kind = VARIANT_FIELDS.get(key)
    if kind is None:
        raise VariantsEditError(f"unknown field '{key}' (known: {', '.join(VARIANT_FIELDS)})")
    if kind == "battery":
        raise VariantsEditError("set battery fields one at a time, e.g. battery.usable_kwh=28")
    if kind == "status":
        return raw.strip()
    if kind == "text":
        return folded(raw.strip(), key_indent=_FIELD_INDENT, key=key)
    if kind == "texts":
        return _text_list(key, [p.strip() for p in raw.split(",") if p.strip()])
    if kind == "sentences":
        # Free text that may hold commas: one item per KEY=VALUE (see _assign_all).
        return _text_list(key, [raw.strip()] if raw.strip() else [])
    if kind == "year_months":
        # Quoted, so a bare year stays text instead of reading back as a number.
        return _flow_seq([DoubleQuotedScalarString(v) for v in _pair(key, raw)])
    if kind == "years":
        try:
            return _flow_seq([int(p) for p in _pair(key, raw)])
        except ValueError:
            raise VariantsEditError(f"{key}: give two years, e.g. 2017,2019") from None
    raise AssertionError(kind)  # pragma: no cover - every kind is handled above


def _load(path: Path) -> CommentedMap:
    if not path.exists():
        doc = CommentedMap()
        doc["variants"] = CommentedMap()
        return doc
    data = _yaml().load(path.read_text()) or CommentedMap()
    if not isinstance(data, dict):
        raise VariantsEditError(f"{path.name}: top-level must be a mapping")
    if data.get("variants") is None:
        data["variants"] = CommentedMap()
    if not isinstance(data["variants"], dict):
        raise VariantsEditError(f"{path.name}: 'variants:' must be a mapping")
    return data


def _find(variants: Mapping, vid: str) -> Any:
    return next((k for k in variants if normalize_variant_id(k) == vid), None)


def _entry(data: CommentedMap, vid: str) -> CommentedMap:
    key = _find(data["variants"], vid)
    if key is None:
        known = ", ".join(str(k) for k in data["variants"]) or "none"
        raise VariantsEditError(f"variant {vid!r} not found (declared: {known})")
    return data["variants"][key]


def _place(entry: CommentedMap, key: str, value: Any) -> None:
    """Set ``key``, inserting a new one at its canonical position."""
    if key in entry:
        entry[key] = value
        return
    order = list(VARIANT_FIELDS)
    rank = order.index(key) if key in order else len(order)
    pos = sum(1 for k in entry if k in order and order.index(k) < rank)
    entry.insert(pos, key, value)


def _assign(entry: CommentedMap, key: str, raw: str) -> None:
    value = parse_value(key, raw)
    if not key.startswith("battery."):
        _place(entry, key, value)
        return
    sub = key.split(".", 1)[1]
    battery = entry.get("battery")
    if not isinstance(battery, dict):
        battery = CommentedMap()
        battery.fa.set_flow_style()
        _place(entry, "battery", battery)
    order = list(BATTERY_FIELDS)
    if sub in battery:
        battery[sub] = value
    else:
        pos = sum(1 for k in battery if k in order and order.index(k) < order.index(sub))
        battery.insert(pos, sub, value)


def _assign_all(entry: CommentedMap, assignments: Sequence[tuple[str, str]]) -> None:
    """Apply ``(key, value)`` pairs; a sentence-list key given several times lists each."""
    gathered: dict[str, list[str]] = {}
    for key, raw in assignments:
        if VARIANT_FIELDS.get(key) == "sentences":
            gathered.setdefault(key, []).append(raw.strip())
        else:
            _assign(entry, key, raw)
    for key, items in gathered.items():
        _place(entry, key, _text_list(key, [i for i in items if i]))


def _write(path: Path, original: str | None, data: CommentedMap) -> Path:
    """Dump ``data``, re-parse and check it, and revert on any failure."""
    with open(path, "w") as f:
        if original is None:
            f.write(_HEADER)
        _dump(data, f)
    try:
        errors = check_variants_doc(yaml_io.safe_load(path.read_text()))
    except Exception as e:  # pragma: no cover - defensive
        errors = [f"edit failed post-check: {e}"]
    if errors:
        if original is None:
            path.unlink(missing_ok=True)
        else:
            path.write_text(original)
        raise VariantsEditError("; ".join(errors) + " (reverted)")
    return path


def _edit(profile: Profile | None, change) -> Path:
    path = _path(profile)
    original = path.read_text() if path.exists() else None
    data = _load(path)
    change(data)
    return _write(path, original, data)


# ── public editors ──────────────────────────────────────────────────────────


def add_variant(
    variant_id: str,
    name: str,
    meta: Sequence[tuple[str, str]] = (),
    *,
    profile: Profile | None = None,
) -> Path:
    """Add a variant (scaffolding the file if absent). Errors if the ID exists."""
    vid = _checked_id(variant_id)
    if not name.strip():
        raise VariantsEditError("a variant needs a name")

    def change(data: CommentedMap) -> None:
        if _find(data["variants"], vid) is not None:
            raise VariantsEditError(f"variant {vid!r} already exists")
        entry = CommentedMap()
        entry["name"] = name.strip()
        _assign_all(entry, meta)
        data["variants"][vid] = entry

    return _edit(profile, change)


def set_variant_meta(
    variant_id: str, assignments: Sequence[tuple[str, str]], *, profile: Profile | None = None
) -> Path:
    """Set metadata fields (``battery.<x>`` for a battery field) on a variant.

    Each field given replaces its current value; a list of sentences (``sources``)
    takes one item per pair, so give its key once per item.
    """
    vid = _checked_id(variant_id)
    if not assignments:
        raise VariantsEditError("nothing to set (give KEY=VALUE pairs)")

    def change(data: CommentedMap) -> None:
        _assign_all(_entry(data, vid), assignments)

    return _edit(profile, change)


def unset_variant_meta(
    variant_id: str, keys: Sequence[str], *, profile: Profile | None = None
) -> Path:
    """Remove metadata fields from a variant. ``name`` cannot be removed."""
    vid = _checked_id(variant_id)

    def change(data: CommentedMap) -> None:
        entry = _entry(data, vid)
        for key in keys:
            if key == "name":
                raise VariantsEditError("a variant's name cannot be removed")
            if key.startswith("battery."):
                battery = entry.get("battery")
                sub = key.split(".", 1)[1]
                if not isinstance(battery, dict) or sub not in battery:
                    raise VariantsEditError(f"variant {vid!r} has no {key}")
                del battery[sub]
                if not battery:
                    del entry["battery"]
            elif key in entry:
                del entry[key]
            else:
                raise VariantsEditError(f"variant {vid!r} has no {key}")

    return _edit(profile, change)


def rename_variant(old: str, new: str, *, profile: Profile | None = None) -> Path:
    """Change a variant's ID, keeping its position and metadata."""
    old_id, new_id = _checked_id(old), _checked_id(new)

    def change(data: CommentedMap) -> None:
        key = _find(data["variants"], old_id)
        if key is None:
            raise VariantsEditError(f"variant {old_id!r} not found")
        if old_id != new_id and _find(data["variants"], new_id) is not None:
            raise VariantsEditError(f"variant {new_id!r} already exists")
        rebuilt = CommentedMap()
        for k, v in data["variants"].items():
            rebuilt[new_id if k == key else k] = v
        data["variants"] = rebuilt

    return _edit(profile, change)


def remove_variant(variant_id: str, *, profile: Profile | None = None) -> Path:
    """Remove a variant."""
    vid = _checked_id(variant_id)

    def change(data: CommentedMap) -> None:
        key = _find(data["variants"], vid)
        if key is None:
            raise VariantsEditError(f"variant {vid!r} not found")
        del data["variants"][key]

    return _edit(profile, change)
