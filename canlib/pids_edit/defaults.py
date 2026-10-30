"""PID-level defaults for parameter fields: set them, and hoist them out of the parameters.

A PID can state :data:`canlib.pids.INHERITED_PARAM_FIELDS` (``unit``, ``min``, ``verified``,
``source`` …) once; a parameter that sets none of its own inherits it
(:func:`canlib.pids.effective_param`). :func:`hoist_defaults` moves existing repetition up;
:func:`set_pid_defaults` edits the PID-level values directly. Same discipline as the other
editors: a surgical text edit, a YAML re-parse, and a restore on any failure.
"""

from __future__ import annotations

import json
import re
from collections import Counter
from collections.abc import Iterable, Mapping
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

# Fewest parameters that must share a value before it is worth stating on the PID: a pair
# saves a line and makes both of them harder to read on their own.
MIN_SHARING = 3

_PID_INDENT = " " * 6
_PARAM_INDENT = 10


@dataclass(frozen=True)
class Hoisted:
    """One field moved from a PID's parameters up to the PID."""

    pid: str
    field: str
    value: Any
    params: tuple[str, ...]  # the parameters that dropped their own copy
    total: int  # parameters in the PID


def _empty(value: object) -> bool:
    return value is None or value == "" or value == []


def _key(value: object) -> str:
    """A hashable identity that tells ``0`` from ``"0"`` and ``true`` from ``1``."""
    return json.dumps(value, sort_keys=True)


def _params(pid_def: dict) -> dict[str, dict]:
    raw = (pid_def or {}).get("parameters")
    if not isinstance(raw, dict):
        return {}
    return {str(k): v for k, v in raw.items() if isinstance(v, dict)}


def plan_hoist(pid_def: dict) -> dict[str, tuple[Any, tuple[str, ...]]]:
    """The fields of one PID that can move up without changing any parameter's meaning.

    ``{field: (value, parameters_that_drop_it)}``. A field qualifies when the PID does not
    state it yet, every parameter states a non-empty value of its own (an absent one would
    start inheriting, an explicit empty one would be a pointless override), and one value is
    shared by more than half of the parameters (and at least :data:`MIN_SHARING`). The minority keep their
    value as an override.
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
        top, n = Counter(_key(v) for v in values.values()).most_common(1)[0]
        if n < MIN_SHARING or n * 2 <= len(params):
            continue
        sharing = tuple(name for name, v in values.items() if _key(v) == top)
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


def _render(indent: str, field: str, value: Any) -> list[str]:
    """The YAML lines for one shared field, keeping the value's own type."""
    if field == "source_links":
        return _format_list_field(indent, field, list(value))
    if isinstance(value, bool):
        return [f"{indent}{field}: {'true' if value else 'false'}"]
    if isinstance(value, (int, float)):
        return [f"{indent}{field}: {value}"]
    return [_format_scalar_field(indent, field, str(value))]


def _insert_before_parameters(block: str, lines: list[str]) -> str:
    """Add ``lines`` to a PID block just above ``parameters:`` (or at its end)."""
    rows = block.splitlines(keepends=True)
    at = next((i for i, ln in enumerate(rows) if re.match(r"^ {6}parameters:", ln)), len(rows))
    return "".join(rows[:at] + [f"{ln}\n" for ln in lines] + rows[at:])


def _set_pid_field(block: str, field: str, lines: list[str]) -> str:
    """Replace the PID's ``field`` with ``lines`` in place, or add it above ``parameters:``.

    No ``lines`` removes the field.
    """
    old = block.splitlines(keepends=True)
    at = next((i for i, ln in enumerate(old) if re.match(rf"^ {{6}}{field}:", ln)), None)
    stripped = _remove_field_line(block, field, indent=6)
    if not lines:
        return stripped
    if at is None:
        return _insert_before_parameters(stripped, lines)
    kept = stripped.splitlines(keepends=True)
    return "".join(kept[:at] + [f"{ln}\n" for ln in lines] + kept[at:])


def set_pid_defaults(
    ecu_name: str,
    pid: str,
    fields: Mapping[str, Any],
    *,
    clear: Iterable[str] = (),
    pids_dir: Path | None = None,
) -> Path:
    """Set (and/or clear) a PID's shared parameter fields.

    ``fields`` maps names from :data:`~canlib.pids.INHERITED_PARAM_FIELDS` to values; ``clear``
    names fields to remove. An existing field keeps its position, a new one goes above
    ``parameters:``. Every parameter that sets none of its own then inherits the value.
    """
    clear = tuple(clear)
    unknown = [f for f in (*fields, *clear) if f not in INHERITED_PARAM_FIELDS]
    if unknown:
        raise PidsEditError(
            f"not a shareable field: {', '.join(unknown)} (allowed: {', '.join(INHERITED_PARAM_FIELDS)})"
        )
    if set(fields) & set(clear):
        raise PidsEditError("a field cannot be both set and cleared")
    if not fields and not clear:
        raise PidsEditError("nothing to set or clear")
    for field, value in fields.items():
        if _empty(value):
            raise PidsEditError(f"{field} must not be empty (use clear to remove it)")

    fpath = find_ecu_file(ecu_name, pids_dir=pids_dir)
    original = fpath.read_text()
    pid_u = str(pid).strip().upper()
    ordered = [f for f in INHERITED_PARAM_FIELDS if f in fields]

    def transform(text: str) -> str:
        start, end = _locate_pid(text, ecu_name, pid_u)
        block = text[start:end]
        for field in clear:
            block = _set_pid_field(block, field, [])
        for field in ordered:
            block = _set_pid_field(block, field, _render(_PID_INDENT, field, fields[field]))
        return text[:start] + block + text[end:]

    def checker(ecu_def: dict) -> None:
        pdef = _pid_def(ecu_def, pid_u)
        if pdef is None:
            raise PidsEditError(f"PID {pid_u!r} missing after edit")
        for field, value in fields.items():
            if _key(pdef.get(field)) != _key(value):
                raise PidsEditError(
                    f"PID {pid_u!r} {field} mismatch after edit: {pdef.get(field)!r}"
                )
        for field in clear:
            if field in pdef:
                raise PidsEditError(f"PID {pid_u!r} {field} still present after clear")

    _safe_write(fpath, original, transform(original), ecu_name.strip().upper(), checker)
    return fpath


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
        block = _insert_before_parameters(block, _render(_PID_INDENT, field, value))
    return block


def hoist_defaults(
    ecu_name: str,
    pid: str | None = None,
    *,
    dry_run: bool = False,
    pids_dir: Path | None = None,
) -> list[Hoisted]:
    """Move fields repeated across a PID's parameters up to the PID.

    Applies :func:`plan_hoist` to ``pid`` (or every PID of the ECU) and rewrites the file
    once. The result is checked before it is kept: every parameter must resolve to exactly
    the same shared fields as before, so the move never changes what any signal reports.
    With ``dry_run`` nothing is written. Returns what moved (or would).
    """
    fpath = find_ecu_file(ecu_name, pids_dir=pids_dir)
    original = fpath.read_text()
    ecu_def = _ecu_def(yaml.safe_load(original), ecu_name)
    wanted = str(pid).strip().upper() if pid else None
    if wanted and _pid_def(ecu_def, wanted) is None:
        raise PidsEditError(f"PID {wanted!r} not found under {ecu_name!r}")

    plans: dict[str, dict] = {}
    before: dict[str, dict[str, dict]] = {}
    totals: dict[str, int] = {}
    for key, pdef in (ecu_def.get("pids") or {}).items():
        key_u = str(key).upper()
        if not isinstance(pdef, dict) or (wanted and key_u != wanted):
            continue
        plan = plan_hoist(pdef)
        if plan:
            plans[key_u] = plan
            totals[key_u] = len(_params(pdef))
            before[key_u] = {n: effective_param(pdef, p) for n, p in _params(pdef).items()}

    hoisted = [
        Hoisted(pid_u, field, value, names, totals[pid_u])
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
                    if _key(got.get(field)) != _key(want.get(field)):
                        raise PidsEditError(
                            f"{pid_u}/{name}: {field} changed by the hoist, reverted"
                        )

    _safe_write(fpath, original, transform(original), ecu_name.strip().upper(), checker)
    return hoisted
