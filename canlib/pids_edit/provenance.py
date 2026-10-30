"""PID-level ``source`` / ``source_links``: set them, and hoist them out of the parameters.

A parameter that omits ``source``/``source_links`` inherits its PID's
(:func:`canlib.pids.effective_param`), so provenance shared by a whole page is stated once.
:func:`hoist_provenance` moves existing duplicates up; the setters edit the PID-level value
directly. Same discipline as the other editors: a surgical text edit, a YAML re-parse, and a
restore on any failure.
"""

from __future__ import annotations

import json
import re
from collections import Counter
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

from canlib.pids import INHERITED_PARAM_FIELDS, effective_param

from ._text import (
    PidsEditError,
    _find_ecu_block,
    _format_list_field,
    _format_scalar_field,
    _keyed_block,
    _remove_field_line,
    _safe_write,
    find_ecu_file,
)

_PID_INDENT = " " * 6
_PARAM_INDENT = 10


@dataclass(frozen=True)
class Hoisted:
    """One field moved from a PID's parameters up to the PID."""

    pid: str
    field: str
    value: Any
    params: tuple[str, ...]  # the parameters that dropped their own copy


def _empty(value: object) -> bool:
    return value is None or value == "" or value == []


def _same(a: object, b: object) -> bool:
    return json.dumps(a, sort_keys=True) == json.dumps(b, sort_keys=True)


def _params(pid_def: dict) -> dict[str, dict]:
    raw = (pid_def or {}).get("parameters")
    return (
        {str(k): v for k, v in raw.items() if isinstance(v, dict)} if isinstance(raw, dict) else {}
    )


def plan_hoist(pid_def: dict) -> dict[str, tuple[Any, tuple[str, ...]]]:
    """The fields of one PID that can move up without changing any parameter's meaning.

    ``{field: (value, parameters_that_drop_it)}``. A field qualifies when the PID does not
    state it yet, every parameter states a non-empty value of its own (an absent one would
    start inheriting, an explicit empty one would be a pointless override), and at least two
    share the most common value. Parameters with a different value keep it as an override.
    """
    params = _params(pid_def)
    if len(params) < 2:
        return {}
    plan: dict[str, tuple[Any, tuple[str, ...]]] = {}
    for field in INHERITED_PARAM_FIELDS:
        if not _empty(pid_def.get(field)):
            continue
        values = {name: p.get(field) for name, p in params.items()}
        if any(_empty(v) for v in values.values()):
            continue
        counts = Counter(json.dumps(v, sort_keys=True) for v in values.values())
        top, n = counts.most_common(1)[0]
        if n < 2:
            continue
        sharing = tuple(name for name, v in values.items() if json.dumps(v, sort_keys=True) == top)
        plan[field] = (values[sharing[0]], sharing)
    return plan


def _pid_def(ecu_def: dict, pid_u: str) -> dict | None:
    pids = ecu_def.get("pids") or {}
    return next((v for k, v in pids.items() if str(k).upper() == pid_u), None)


def _ecu_def(data: dict, ecu_name: str) -> dict:
    found = next((v for k, v in data.items() if str(k).upper() == ecu_name.strip().upper()), None)
    if not isinstance(found, dict):
        raise PidsEditError(f"ECU {ecu_name!r} not found")
    return found


def _locate_pid(text: str, ecu_name: str, pid_u: str) -> tuple[int, int]:
    ecu_start, ecu_end = _find_ecu_block(text, ecu_name)
    pids = _keyed_block(text, "pids", 2, ecu_start, ecu_end)
    if not pids:
        raise PidsEditError(f"ECU {ecu_name!r} has no pids: section")
    pidb = _keyed_block(text, pid_u, 4, pids[2], pids[3])
    if not pidb:
        raise PidsEditError(f"PID {pid_u!r} not found under {ecu_name!r}")
    return pidb[0], pidb[3]


def _insert_before_parameters(block: str, lines: list[str]) -> str:
    """Add ``lines`` to a PID block just above ``parameters:`` (or at its end)."""
    rows = block.splitlines(keepends=True)
    at = next((i for i, ln in enumerate(rows) if re.match(r"^ {6}parameters:", ln)), len(rows))
    return "".join(rows[:at] + [f"{ln}\n" for ln in lines] + rows[at:])


def _set_pid_field(block: str, field: str, lines: list[str]) -> str:
    """Replace the PID's ``field`` with ``lines`` in place, or add it above ``parameters:``."""
    present = re.search(rf"^ {{6}}{field}:", block, re.MULTILINE) is not None
    stripped = _remove_field_line(block, field, indent=6)
    if not lines:
        return stripped
    if not present:
        return _insert_before_parameters(stripped, lines)
    # Keep the field where it was: remove-then-insert at the old spot.
    old = block.splitlines(keepends=True)
    at = next(i for i, ln in enumerate(old) if re.match(rf"^ {{6}}{field}:", ln))
    kept = stripped.splitlines(keepends=True)
    return "".join(kept[:at] + [f"{ln}\n" for ln in lines] + kept[at:])


def _edit_pid_field(
    ecu_name: str,
    pid: str,
    field: str,
    lines: list[str],
    expected: object,
    *,
    pids_dir: Path | None,
) -> Path:
    fpath = find_ecu_file(ecu_name, pids_dir=pids_dir)
    original = fpath.read_text()
    pid_u = str(pid).strip().upper()

    def transform(text: str) -> str:
        start, end = _locate_pid(text, ecu_name, pid_u)
        return text[:start] + _set_pid_field(text[start:end], field, lines) + text[end:]

    def checker(ecu_def: dict) -> None:
        pdef = _pid_def(ecu_def, pid_u)
        if pdef is None:
            raise PidsEditError(f"PID {pid_u!r} missing after edit")
        got = pdef.get(field)
        if not _same(got, expected):
            raise PidsEditError(f"PID {pid_u!r} {field} mismatch after edit: {got!r}")

    _safe_write(fpath, original, transform(original), ecu_name.strip().upper(), checker)
    return fpath


def set_pid_source(
    ecu_name: str, pid: str, source: str | None, *, pids_dir: Path | None = None
) -> Path:
    """Set (or, with ``None``/blank, clear) a PID's ``source:``.

    Every parameter of the PID that has no ``source`` of its own inherits it. An existing
    field keeps its position; a new one goes above ``parameters:``.
    """
    text = "" if source is None else str(source).strip()
    lines = [_format_scalar_field(_PID_INDENT, "source", text)] if text else []
    return _edit_pid_field(ecu_name, pid, "source", lines, text or None, pids_dir=pids_dir)


def set_pid_source_links(
    ecu_name: str, pid: str, links: Sequence[str] | None, *, pids_dir: Path | None = None
) -> Path:
    """Set (or, with none, clear) a PID's ``source_links:``, inherited like ``source:``."""
    urls = [str(u).strip() for u in (links or []) if str(u).strip()]
    lines = _format_list_field(_PID_INDENT, "source_links", urls) if urls else []
    return _edit_pid_field(ecu_name, pid, "source_links", lines, urls or None, pids_dir=pids_dir)


def _hoist_in_block(block: str, plan: dict[str, tuple[Any, tuple[str, ...]]]) -> str:
    """Drop the shared field from its parameters and state it once above ``parameters:``."""
    for field, (value, names) in plan.items():
        for name in names:
            params = _keyed_block(block, "parameters", 6, 0, len(block))
            existing = _keyed_block(block, name, 8, params[2], params[3]) if params else None
            if not existing:
                raise PidsEditError(f"parameter {name!r} not found while hoisting {field}")
            start, end = existing[0], existing[3]
            block = (
                block[:start]
                + _remove_field_line(block[start:end], field, _PARAM_INDENT)
                + block[end:]
            )
        if field == "source_links":
            lines = _format_list_field(_PID_INDENT, field, list(value))
        else:
            lines = [_format_scalar_field(_PID_INDENT, field, value)]
        block = _insert_before_parameters(block, lines)
    return block


def hoist_provenance(
    ecu_name: str,
    pid: str | None = None,
    *,
    dry_run: bool = False,
    pids_dir: Path | None = None,
) -> list[Hoisted]:
    """Move provenance repeated across a PID's parameters up to the PID.

    Applies :func:`plan_hoist` to ``pid`` (or every PID of the ECU) and rewrites the file
    once. The result is checked before it is kept: every parameter must resolve to exactly
    the same ``source``/``source_links`` as before, so the move never changes what any
    signal reports. With ``dry_run`` nothing is written. Returns what moved (or would).
    """
    fpath = find_ecu_file(ecu_name, pids_dir=pids_dir)
    original = fpath.read_text()
    ecu_def = _ecu_def(yaml.safe_load(original), ecu_name)
    wanted = str(pid).strip().upper() if pid else None
    if wanted and _pid_def(ecu_def, wanted) is None:
        raise PidsEditError(f"PID {wanted!r} not found under {ecu_name!r}")

    plans: dict[str, dict] = {}
    before: dict[str, dict[str, dict]] = {}
    for key, pdef in (ecu_def.get("pids") or {}).items():
        key_u = str(key).upper()
        if not isinstance(pdef, dict) or (wanted and key_u != wanted):
            continue
        plan = plan_hoist(pdef)
        if plan:
            plans[key_u] = plan
            before[key_u] = {n: effective_param(pdef, p) for n, p in _params(pdef).items()}

    hoisted = [
        Hoisted(pid_u, field, value, names)
        for pid_u, plan in plans.items()
        for field, (value, names) in plan.items()
    ]
    if dry_run or not plans:
        return hoisted

    def transform(text: str) -> str:
        for pid_u, plan in plans.items():
            start, end = _locate_pid(text, ecu_name, pid_u)
            text = text[:start] + _hoist_in_block(text[start:end], plan) + text[end:]
        return text

    def checker(new_def: dict) -> None:
        for pid_u, expected in before.items():
            pdef = _pid_def(new_def, pid_u)
            if pdef is None:
                raise PidsEditError(f"PID {pid_u!r} missing after hoist")
            now = _params(pdef)
            for name, want in expected.items():
                got = effective_param(pdef, now.get(name, {}))
                for field in INHERITED_PARAM_FIELDS:
                    if not _same(got.get(field), want.get(field)):
                        raise PidsEditError(
                            f"{pid_u}/{name}: {field} changed by the hoist, reverted"
                        )

    _safe_write(fpath, original, transform(original), ecu_name.strip().upper(), checker)
    return hoisted
