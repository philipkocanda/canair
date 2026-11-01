"""Editors for an ECU's ``research:`` list — the open reverse-engineering backlog.

Appending a lead, moving its status, dropping it, and setting its free-text
fields (``result``, ``notes``, ``capture_protocol``) without hand-editing the
YAML. Items are located by ``target`` (and optionally ``type``/``index``, since a
signal worked over several passes leaves several items with one target), and
every write is re-parsed and rolled back on failure, like the other editors.
Builds on the shared primitives in :mod:`canlib.pids_edit._text`.
"""

from __future__ import annotations

import re
from pathlib import Path

from ._text import (
    PidsEditError,
    _attached_comments_start,
    _cut_block,
    _find_ecu_block,
    _format_block_scalar,
    _format_label,
    _format_list_field,
    _format_scalar_field,
    _insert_lines,
    _keyed_block,
    _norm_states,
    _replace_field_in_block_at,
    _safe_write,
    _today,
    find_ecu_file,
)

# Canonical field order for a rendered research entry.
RESEARCH_FIELD_ORDER = (
    "type",
    "target",
    "status",
    "priority",
    "vehicle_states",
    "revisions",
    "created",
    "updated",
    "date",
    "result",
    "notes",
    "sources",
    "what_to_test",
    "capture_protocol",
)


def _format_research_item(fields: dict, indent: int = 4) -> list[str]:
    """Render one ``- ...`` research list item (dash at ``indent``, fields +2)."""
    dash = " " * indent + "- "
    fld = " " * (indent + 2)
    lines: list[str] = []
    for key in RESEARCH_FIELD_ORDER:
        if key not in fields or fields[key] is None:
            continue
        val = fields[key]
        prefix = dash if not lines else fld  # first field sits on the dash line
        if key == "vehicle_states":
            joined = ", ".join(_format_label(t, flow=True) for t in _norm_states(val))
            lines.append(f"{prefix}{key}: [{joined}]")
        elif key == "revisions":
            lines.append(f"{prefix}{key}: [{', '.join(str(r).strip().lower() for r in val)}]")
        elif key in ("notes", "result", "capture_protocol"):
            block = _format_block_scalar(fld, key, str(val))
            block[0] = prefix + block[0][len(fld) :]
            lines.extend(block)
        elif key in ("sources", "what_to_test"):
            block = _format_list_field(fld, key, val if isinstance(val, (list, tuple)) else [val])
            block[0] = prefix + block[0][len(fld) :]
            lines.extend(block)
        else:
            line = _format_scalar_field(fld, key, val)
            lines.append(prefix + line[len(fld) :])
    return lines


def add_research_entry(
    ecu_name: str,
    *,
    type: str,
    target: str,
    status: str,
    priority: str | None = None,
    vehicle_states: list | None = None,
    created: str | None = None,
    updated: str | None = None,
    date: str | None = None,
    result: str | None = None,
    notes: str | None = None,
    sources: list | None = None,
    what_to_test: list | None = None,
    capture_protocol: str | None = None,
    revisions: list | None = None,
    pids_dir: Path | None = None,
) -> Path:
    """Append a new item to the ECU's ``research:`` list (creating it if absent).

    ``created`` and ``updated`` default to today's date (``YYYY-MM-DD``) so every
    entry is timestamped without the caller having to pass anything.
    """
    today = _today()
    provided = {
        "type": type,
        "target": target,
        "status": status,
        "priority": priority,
        "vehicle_states": vehicle_states,
        "created": created or today,
        "updated": updated or today,
        "date": date,
        "result": result,
        "notes": notes,
        "sources": sources,
        "what_to_test": what_to_test,
        "capture_protocol": capture_protocol,
        "revisions": revisions,
    }
    fields = {k: v for k, v in provided.items() if v is not None}
    for req in ("type", "target", "status"):
        if not str(fields.get(req, "")).strip():
            raise PidsEditError(f"research entry requires non-empty {req!r}")

    fpath = find_ecu_file(ecu_name, pids_dir=pids_dir)
    original = fpath.read_text()
    ecu_key = ecu_name.strip().upper()
    item_lines = _format_research_item(fields, indent=4)

    def transform(text: str) -> str:
        ecu_start, ecu_end = _find_ecu_block(text, ecu_name)
        research = _keyed_block(text, "research", 2, ecu_start, ecu_end)
        if research:
            _, _, r_body_start, r_body_end, _ = research
            return _insert_lines(text, r_body_start, r_body_end, item_lines)
        # No research: section — append one at the end of the ECU block.
        body = text[ecu_start:ecu_end].rstrip("\n")
        new_block = body + "\n\n  research:\n" + "".join(ln + "\n" for ln in item_lines)
        return text[:ecu_start] + new_block + text[ecu_end:]

    def checker(ecu_def: dict) -> None:
        research = ecu_def.get("research")
        if not isinstance(research, list) or not any(
            str(e.get("target")) == str(target) and e.get("type") == type for e in research
        ):
            raise PidsEditError("research entry missing after edit")

    new_text = transform(original)
    _safe_write(fpath, original, new_text, ecu_key, checker)
    return fpath


def _find_research_block(text: str, ecu_name: str):
    """Return the ``_keyed_block`` tuple for ``ECU_NAME``'s ``research:`` section.

    Raises ``PidsEditError`` if the ECU has no ``research:`` section.
    """
    ecu_start, ecu_end = _find_ecu_block(text, ecu_name)
    research = _keyed_block(text, "research", 2, ecu_start, ecu_end)
    if not research:
        raise PidsEditError(f"ECU {ecu_name!r} has no research: section")
    return research


def _research_item_spans(text: str, research_block) -> list[tuple[int, int]]:
    """Return ``[(start, end), ...]`` spans of every item in a ``research:`` list.

    An item ends before the comments heading the next one
    (:func:`~canlib.pids_edit._text._attached_comments_start`).
    """
    _, _, r_body_start, r_body_end, _ = research_block
    item_re = re.compile(r"^ {4}- ", re.MULTILINE)
    starts = [r_body_start + m.start() for m in item_re.finditer(text[r_body_start:r_body_end])]
    ends = [*starts[1:], r_body_end]
    return [(s, _attached_comments_start(text, s, e, 4)) for s, e in zip(starts, ends, strict=True)]


def _match_research_items(
    text: str, research_block, target: str, type: str | None = None
) -> list[tuple[int, int]]:
    """Return the ``research:`` item spans matching ``target`` (and ``type``, if given).

    Spans are returned in list order.
    """
    target_re = re.compile(r"^ {4,6}(?:- )?target:[ \t]*(.*)$", re.MULTILINE)
    type_re = re.compile(r"^ {4,6}(?:- )?type:[ \t]*(.*)$", re.MULTILINE)
    target_norm = str(target).strip().strip('"').strip("'")

    matches = []
    for s, e in _research_item_spans(text, research_block):
        item = text[s:e]
        tm = target_re.search(item)
        if not tm or tm.group(1).strip().strip('"').strip("'") != target_norm:
            continue
        if type is not None:
            ty = type_re.search(item)
            if not ty or ty.group(1).strip().strip('"').strip("'") != type:
                continue
        matches.append((s, e))
    return matches


def _select_research_match(
    target: str, type: str | None, matches: list[tuple[int, int]], *, index: int | None = None
) -> tuple[int, int]:
    """Apply the shared "refuse rather than guess" selector policy.

    ``matches`` is the list of ``(start, end)`` spans already narrowed by
    ``target`` (and ``type``, if given). Real profiles routinely carry several
    research items with the same ``(type, target)`` as a signal is worked
    byte-by-byte, so ``type`` alone does not guarantee uniqueness — ``index``
    (0-based, within this already-narrowed group) is the final disambiguator.
    """
    type_suffix = f" and type {type!r}" if type else ""
    if not matches:
        raise PidsEditError(f"no research item with target {target!r}" + type_suffix)
    if index is not None:
        if not (0 <= index < len(matches)):
            raise PidsEditError(
                f"index {index} out of range for target {target!r}{type_suffix} "
                f"({len(matches)} matching item{'s' if len(matches) != 1 else ''})"
            )
        return matches[index]
    if len(matches) > 1:
        raise PidsEditError(
            f"ambiguous target {target!r} ({len(matches)} matches); pass type= or index="
        )
    return matches[0]


def _research_list_index(text: str, research_block, span: tuple[int, int]) -> int:
    """Ordinal position of ``span`` among ALL items in the research: list.

    Lets a caller build a positionally-robust post-write checker: even when
    several items share the same ``(type, target)``, checking
    ``research[list_index]`` verifies the *specific* item that was edited,
    rather than just "some item with this target/type looks right".
    """
    spans = _research_item_spans(text, research_block)
    return next(i for i, (s, _e) in enumerate(spans) if s == span[0])


def set_research_status(
    ecu_name: str,
    target: str,
    status: str,
    *,
    type: str | None = None,
    index: int | None = None,
    pids_dir: Path | None = None,
) -> Path:
    """Update the ``status:`` of the research item matching ``target`` (and ``type``).

    Also refreshes the item's ``updated`` timestamp to today's date so status
    transitions are dated automatically.

    Selector policy: matches are narrowed by ``target`` (exact) and, if given,
    ``type``. When more than one item still shares that ``target``/``type``
    pair (common — a signal is often worked over several ``decode`` passes),
    pass ``index`` (0-based, within the narrowed group) to pick one; the group
    order matches the item order in the ``research:`` list. Raises
    ``PidsEditError`` if no item matches, ``index`` is out of range, or the
    match is still ambiguous.
    """
    fpath = find_ecu_file(ecu_name, pids_dir=pids_dir)
    original = fpath.read_text()
    ecu_key = ecu_name.strip().upper()
    target_norm = str(target).strip().strip('"').strip("'")
    today = _today()

    research_block = _find_research_block(original, ecu_name)
    matches = _match_research_items(original, research_block, target, type)
    s, e = _select_research_match(target, type, matches, index=index)
    list_index = _research_list_index(original, research_block, (s, e))

    def transform(text: str) -> str:
        item = text[s:e]
        new_item = _replace_field_in_block_at(item, "status", f"      status: {status}", indent=6)
        new_item = _replace_field_in_block_at(
            new_item, "updated", f'      updated: "{today}"', indent=6
        )
        return text[:s] + new_item + text[e:]

    def checker(ecu_def: dict) -> None:
        research = ecu_def.get("research") or []
        if list_index >= len(research):
            raise PidsEditError("status not applied after edit")
        item = research[list_index]
        ok = (
            item.get("target") == target_norm
            and item.get("status") == status
            and item.get("updated") == today
            and (type is None or item.get("type") == type)
        )
        if not ok:
            raise PidsEditError("status not applied after edit")

    new_text = transform(original)
    _safe_write(fpath, original, new_text, ecu_key, checker)
    return fpath


def delete_research_entry(
    ecu_name: str,
    target: str,
    *,
    type: str | None = None,
    index: int | None = None,
    pids_dir: Path | None = None,
) -> Path:
    """Remove an item from the ECU's ``research:`` list.

    A lead is dropped once it's a dead end, superseded, or folded into another
    entry — previously the only way to do this was hand-editing the YAML,
    which the edit-via-tool discipline forbids. Selector policy (``type``/
    ``index``) mirrors :func:`set_research_status`.

    Drops the whole ``research:`` section if this was the last remaining item
    (an empty ``research:`` key parses to ``None`` and fails the schema).
    Verified by YAML re-parse; the file is restored on failure.
    """
    fpath = find_ecu_file(ecu_name, pids_dir=pids_dir)
    original = fpath.read_text()
    ecu_key = ecu_name.strip().upper()
    target_norm = str(target).strip().strip('"').strip("'")

    research_block = _find_research_block(original, ecu_name)
    matches = _match_research_items(original, research_block, target, type)
    s, e = _select_research_match(target, type, matches, index=index)
    pre_count = len(matches)

    def transform(text: str) -> str:
        r_start, _, _r_body_start, r_body_end, _ = research_block
        others = [span for span in _research_item_spans(text, research_block) if span[0] != s]
        if not others:
            # Last item — drop the whole research: block, else an empty
            # ``research:`` key parses to None and fails the schema.
            return _cut_block(text, r_start, r_body_end)
        # Same gap rules as any keyed block: the comments heading what follows
        # stay, and the first item takes no list-leading blank line with it.
        return _cut_block(text, s, e)

    def checker(ecu_def: dict) -> None:
        research = ecu_def.get("research") or []
        post_count = sum(
            1
            for item in research
            if str(item.get("target")) == target_norm and (type is None or item.get("type") == type)
        )
        if post_count != pre_count - 1:
            raise PidsEditError("research entry still present after delete")

    new_text = transform(original)
    _safe_write(fpath, original, new_text, ecu_key, checker)
    return fpath


#: The free-text fields :func:`set_research_text` edits, each rendered by the shared
#: note policy (short text inline, longer text a word-wrapped folded ``>-`` block).
RESEARCH_TEXT_FIELDS = ("result", "notes", "capture_protocol")


def set_research_text(
    ecu_name: str,
    target: str,
    field: str,
    text: str | None,
    *,
    type: str | None = None,
    index: int | None = None,
    pids_dir: Path | None = None,
) -> Path:
    """Set (or clear) one free-text field of a research item.

    Lets a lead's outcome, notes or capture recipe be corrected as work on it
    progresses, without touching ``status``/other fields. ``field`` is one of
    :data:`RESEARCH_TEXT_FIELDS`; the selector policy (``type``/``index``) is
    :func:`set_research_status`'s.

    ``text=None`` (or blank) removes the field. An existing value is replaced in
    place; a new one goes where :data:`RESEARCH_FIELD_ORDER` puts it — before the
    first later field the item has, else after its last line.
    """
    if field not in RESEARCH_TEXT_FIELDS:
        raise PidsEditError(f"{field!r} is not one of {', '.join(RESEARCH_TEXT_FIELDS)}")
    fpath = find_ecu_file(ecu_name, pids_dir=pids_dir)
    original = fpath.read_text()
    ecu_key = ecu_name.strip().upper()
    target_norm = str(target).strip().strip('"').strip("'")
    text_val = "" if text is None else str(text).strip()
    clearing = not text_val
    later = "|".join(RESEARCH_FIELD_ORDER[RESEARCH_FIELD_ORDER.index(field) + 1 :])
    later_re = re.compile(rf"^ {{6}}({later}):") if later else None

    research_block = _find_research_block(original, ecu_name)
    matches = _match_research_items(original, research_block, target, type)
    s, e = _select_research_match(target, type, matches, index=index)
    list_index = _research_list_index(original, research_block, (s, e))

    def transform(text: str) -> str:
        item = text[s:e]
        present = re.search(rf"^ {{6}}{field}:", item, re.MULTILINE) is not None
        if clearing:
            if not present:
                raise PidsEditError(f"research item {target_norm!r} has no {field}: to clear")
            new_item = _replace_field_in_block_at(item, field, [], indent=6)
        else:
            repl = _format_block_scalar(" " * 6, field, text_val)
            if present:
                new_item = _replace_field_in_block_at(item, field, repl, indent=6)
            else:
                lines = item.splitlines(keepends=True)
                tail = len(lines)
                while tail and not lines[tail - 1].strip():
                    tail -= 1  # keep the blank lines separating the next item after it
                anchor = next(
                    (i for i, ln in enumerate(lines) if later_re and later_re.match(ln)), tail
                )
                new_item = "".join(lines[:anchor] + [f"{ln}\n" for ln in repl] + lines[anchor:])
        return text[:s] + new_item + text[e:]

    def checker(ecu_def: dict) -> None:
        research = ecu_def.get("research") or []
        if list_index >= len(research):
            raise PidsEditError(f"research item {target_norm!r} missing after edit")
        item = research[list_index]
        if str(item.get("target")) != target_norm or (
            type is not None and item.get("type") != type
        ):
            raise PidsEditError(f"research item {target_norm!r} missing after edit")
        got = item.get(field)
        if clearing:
            if got is not None:
                raise PidsEditError(f"{field} still present after clear")
            return
        if got is None:
            raise PidsEditError(f"{field} missing after edit")
        if " ".join(str(got).split()) != " ".join(text_val.split()):
            raise PidsEditError(f"{field} mismatch after edit")

    new_text = transform(original)
    _safe_write(fpath, original, new_text, ecu_key, checker)
    return fpath


def set_research_notes(
    ecu_name: str,
    target: str,
    notes: str | None,
    *,
    type: str | None = None,
    index: int | None = None,
    pids_dir: Path | None = None,
) -> Path:
    """Set (or clear) a research item's ``notes:`` (see :func:`set_research_text`)."""
    return set_research_text(
        ecu_name, target, "notes", notes, type=type, index=index, pids_dir=pids_dir
    )


def set_research_result(
    ecu_name: str,
    target: str,
    result: str | None,
    *,
    type: str | None = None,
    index: int | None = None,
    pids_dir: Path | None = None,
) -> Path:
    """Set (or clear) a research item's ``result:`` (see :func:`set_research_text`)."""
    return set_research_text(
        ecu_name, target, "result", result, type=type, index=index, pids_dir=pids_dir
    )


def set_research_capture_protocol(
    ecu_name: str,
    target: str,
    protocol: str | None,
    *,
    type: str | None = None,
    index: int | None = None,
    pids_dir: Path | None = None,
) -> Path:
    """Set (or clear) a research item's ``capture_protocol:`` (see :func:`set_research_text`)."""
    return set_research_text(
        ecu_name, target, "capture_protocol", protocol, type=type, index=index, pids_dir=pids_dir
    )
