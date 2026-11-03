"""Surgical, comment-preserving edits to a profile's ``variants.yaml``.

``canair variants`` adds, renames and removes variants and sets their metadata through
these helpers, so the file is never hand-edited. Every write is re-parsed and checked
with :func:`canlib.variants.check_variants_doc` and reverted on failure, so a broken
edit never lands on disk. Values arrive as command-line text and are typed here, per
field, from the same field table the checker uses.
"""

from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path
from typing import TYPE_CHECKING, Any

from ruamel.yaml.comments import CommentedMap, CommentedSeq
from ruamel.yaml.scalarstring import DoubleQuotedScalarString

from canlib.variants import (
    BATTERY_FIELDS,
    PROFILE_OVERRIDE_FIELDS,
    VARIANT_FIELDS,
    check_variants_doc,
    id_problem,
    normalize_variant_id,
)

from .doc import Target, edit_doc
from .errors import EditError
from .rt import NOTE_WRAP_WIDTH, folded
from .ruamel_ops import find_key, flow_seq, place_in_order, remove_key, rename_key

if TYPE_CHECKING:
    from canlib.profile import Profile

_HEADER = (
    "# The variants of this car (battery, facelift, market) and their metadata.\n"
    "# List or edit with `canair variants`. See docs/concepts/variants.md.\n"
)


class VariantsEditError(EditError):
    """Raised when a ``variants.yaml`` edit cannot be applied safely."""


def _checked_id(raw: str) -> str:
    vid = normalize_variant_id(raw)
    if problem := id_problem(vid):
        raise VariantsEditError(problem)
    return vid


# Where a field sits: ``  <id>:`` then ``    <field>:``.
_FIELD_INDENT = 4


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


def _override_value(sub: str, raw: str) -> Any:
    kind = PROFILE_OVERRIDE_FIELDS.get(sub)
    if kind is None:
        raise VariantsEditError(
            f"cannot override '{sub}' (only: {', '.join(PROFILE_OVERRIDE_FIELDS)})"
        )
    if kind == "text":
        return folded(raw.strip(), key_indent=_FIELD_INDENT + 2, key=sub)
    if kind == "positive_int":
        value = _number(f"profile_overrides.{sub}", raw)
        if not isinstance(value, int):
            raise VariantsEditError(f"profile_overrides.{sub}: must be a whole number")
        return value
    if kind == "texts":
        return flow_seq([p.strip() for p in raw.split(",") if p.strip()])
    raise VariantsEditError(
        f"profile_overrides.{sub} is a mapping; it cannot be given on the command line"
    )


def parse_value(key: str, raw: str) -> Any:
    """Type a command-line ``raw`` value for field ``key`` (``battery.<x>`` for battery).

    ``profile_overrides.<setting>`` types the value as that ``profile.yaml`` setting.
    """
    if key.startswith("profile_overrides."):
        return _override_value(key.split(".", 1)[1], raw)
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
    if kind == "revisions":
        raise VariantsEditError("set an ECU's revisions with `canair variants set-revisions`")
    if kind == "overrides":
        raise VariantsEditError(
            "set overrides one at a time, e.g. profile_overrides.car_model=NAME"
        )
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
        return flow_seq([DoubleQuotedScalarString(v) for v in _pair(key, raw)])
    if kind == "years":
        try:
            return flow_seq([int(p) for p in _pair(key, raw)])
        except ValueError:
            raise VariantsEditError(f"{key}: give two years, e.g. 2017,2019") from None
    raise AssertionError(kind)  # pragma: no cover - every kind is handled above


def _check(data: object) -> None:
    errors = check_variants_doc(data)
    if errors:
        raise VariantsEditError("; ".join(errors) + " (reverted)")


VARIANTS = Target(
    key="variants",
    path=lambda profile: profile.variants_file,
    error=VariantsEditError,
    section="variants",
    check=_check,
    header=_HEADER,
    sequence=(2, 0),
)


def _entry(doc, vid: str) -> CommentedMap:
    key = find_key(doc.section, vid, normalize_variant_id)
    if key is None:
        known = ", ".join(str(k) for k in doc.section) or "none"
        raise VariantsEditError(f"variant {vid!r} not found (declared: {known})")
    return doc.section[key]


def _place(entry: CommentedMap, key: str, value: Any) -> None:
    """Set ``key``, inserting a new one at its canonical position."""
    place_in_order(entry, key, value, list(VARIANT_FIELDS))


def _assign(entry: CommentedMap, key: str, raw: str) -> None:
    value = parse_value(key, raw)
    if key.startswith("battery."):
        _assign_sub(entry, "battery", list(BATTERY_FIELDS), key.split(".", 1)[1], value)
    elif key.startswith("profile_overrides."):
        sub = key.split(".", 1)[1]
        _assign_sub(entry, "profile_overrides", list(PROFILE_OVERRIDE_FIELDS), sub, value)
    else:
        _place(entry, key, value)


def _assign_sub(entry: CommentedMap, field: str, order: list[str], sub: str, value: Any) -> None:
    """Set ``sub`` inside the mapping ``field`` (created if absent), in canonical order."""
    block = entry.get(field)
    if not isinstance(block, dict):
        block = CommentedMap()
        if field == "battery":
            block.fa.set_flow_style()
        _place(entry, field, block)
    place_in_order(block, sub, value, order)


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
    with edit_doc(VARIANTS, profile, create=True) as doc:
        if find_key(doc.section, vid, normalize_variant_id) is not None:
            raise VariantsEditError(f"variant {vid!r} already exists")
        entry = CommentedMap()
        entry["name"] = name.strip()
        _assign_all(entry, meta)
        doc.section[vid] = entry
    return doc.path


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
    with edit_doc(VARIANTS, profile, create=True) as doc:
        _assign_all(_entry(doc, vid), assignments)
    return doc.path


def unset_variant_meta(
    variant_id: str, keys: Sequence[str], *, profile: Profile | None = None
) -> Path:
    """Remove metadata fields from a variant. ``name`` cannot be removed."""
    vid = _checked_id(variant_id)
    with edit_doc(VARIANTS, profile, create=True) as doc:
        entry = _entry(doc, vid)
        for key in keys:
            if key == "name":
                raise VariantsEditError("a variant's name cannot be removed")
            field, dot, sub = key.partition(".")
            if dot and field in ("battery", "profile_overrides"):
                block = entry.get(field)
                if not isinstance(block, dict) or sub not in block:
                    raise VariantsEditError(f"variant {vid!r} has no {key}")
                del block[sub]
                if not block:
                    del entry[field]
            elif key in entry:
                del entry[key]
            else:
                raise VariantsEditError(f"variant {vid!r} has no {key}")
    return doc.path


def rename_variant(old: str, new: str, *, profile: Profile | None = None) -> Path:
    """Change a variant's ID, keeping its position and metadata."""
    old_id, new_id = _checked_id(old), _checked_id(new)
    with edit_doc(VARIANTS, profile, create=True) as doc:
        key = find_key(doc.section, old_id, normalize_variant_id)
        if key is None:
            raise VariantsEditError(f"variant {old_id!r} not found")
        if old_id != new_id and find_key(doc.section, new_id, normalize_variant_id) is not None:
            raise VariantsEditError(f"variant {new_id!r} already exists")
        rename_key(doc.section, key, new_id)
    return doc.path


def remove_variant(variant_id: str, *, profile: Profile | None = None) -> Path:
    """Remove a variant."""
    vid = _checked_id(variant_id)
    with edit_doc(VARIANTS, profile, create=True) as doc:
        key = find_key(doc.section, vid, normalize_variant_id)
        if key is None:
            raise VariantsEditError(f"variant {vid!r} not found")
        remove_key(doc.section, key, parent=doc.data, parent_key="variants")
    return doc.path


def set_variant_revisions(
    variant_id: str, ecu: str, revisions: Sequence[str] | None, *, profile: Profile | None = None
) -> Path:
    """Say which revisions of ``ecu`` the variant can carry (None: any — the entry is removed).

    That the ECU and each revision exist is the caller's check (it needs the ECU files);
    the shape is checked here like every other field.
    """
    vid = _checked_id(variant_id)
    revs = [str(r).strip().lower() for r in (revisions or []) if str(r).strip()]
    for rid in revs:
        if problem := id_problem(rid, "revision", "uds"):
            raise VariantsEditError(problem)
    with edit_doc(VARIANTS, profile, create=True) as doc:
        entry = _entry(doc, vid)
        block = entry.get("revisions")
        if not revs:
            if not isinstance(block, dict) or ecu not in block:
                raise VariantsEditError(f"variant {vid!r} does not limit {ecu}'s revisions")
            del block[ecu]
            if not block:
                del entry["revisions"]
        else:
            if not isinstance(block, dict):
                block = CommentedMap()
                _place(entry, "revisions", block)
            block[ecu] = flow_seq(list(dict.fromkeys(revs)))
    return doc.path


def rename_variant_revisions_ecu(old: str, new: str, *, profile: Profile | None = None) -> bool:
    """Follow an ECU rename: rename its key in every variant's ``revisions:``.

    Returns whether anything changed (the file is left untouched otherwise).
    """
    from canlib.profile import active

    if not (profile or active()).variants_file.exists():
        return False
    found = False
    with edit_doc(VARIANTS, profile) as doc:
        for entry in doc.section.values():
            block = entry.get("revisions") if isinstance(entry, dict) else None
            if isinstance(block, dict) and old in block:
                rename_key(block, old, new)
                found = True
        doc.discard = not found
    return found
