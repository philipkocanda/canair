"""Replace the retired ``ha_class`` field with ``category`` (``canair pids migrate-categories``).

``ha_class`` was a Home Assistant ``device_class`` stated on nearly every signal, though the
unit already implied it for almost all of them. ``category`` is stated only where the unit
does not (:mod:`canlib.signal_category`). Each ``ha_class`` becomes:

* nothing, when the signal's unit implies a category (or the value was empty or unknown);
* the matching category otherwise (``battery`` → ``state_of_charge``, ``light`` → ``lamp``).

A PID-level ``ha_class`` (an inherited default) is resolved into its signals and removed;
``category`` is never inherited. Same discipline as the other editors: a surgical text
edit, a YAML re-parse, and a restore on any failure.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import yaml

from canlib.pids import effective_param
from canlib.signal_category import CATEGORIES, unit_category

from ._text import (
    PidsEditError,
    _format_scalar_field,
    _keyed_block,
    _remove_field_line,
    _replace_field_in_block_at,
    _safe_write,
    find_ecu_file,
)
from .defaults import _ecu_def, _locate_pid, _params

RETIRED_FIELD = "ha_class"

# Home Assistant device classes whose canair category has another name.
_RENAMED = {"battery": "state_of_charge", "battery_charging": "charging", "light": "lamp"}

_PID_INDENT = 6
_PARAM_INDENT = 10


@dataclass(frozen=True)
class Migrated:
    """One signal's (or PID default's) ``ha_class``, and what replaced it."""

    pid: str
    param: str | None  # None: the PID-level default
    ha_class: object
    category: str | None  # None: dropped
    reason: str


def convert(ha_class: object, unit: object) -> tuple[str | None, str]:
    """The category an ``ha_class`` becomes for a signal with ``unit``, and why."""
    if not isinstance(ha_class, str) or ha_class.strip() in ("", "none"):
        return None, "empty"
    implied = unit_category(unit)
    if implied is not None:
        return None, f"implied by unit {unit!r}"
    category = _RENAMED.get(ha_class.strip(), ha_class.strip())
    if category not in CATEGORIES:
        return None, "no matching category"
    return category, "kept"


def plan_ecu(ecu_def: dict) -> list[Migrated]:
    """Every ``ha_class`` of one ECU definition and what it becomes."""
    plan: list[Migrated] = []
    for key, pdef in (ecu_def.get("pids") or {}).items():
        if not isinstance(pdef, dict):
            continue
        pid_u = str(key).upper()
        inherited = pdef.get(RETIRED_FIELD)
        if RETIRED_FIELD in pdef:
            plan.append(Migrated(pid_u, None, inherited, None, "category is not inherited"))
        for name, param in _params(pdef).items():
            own = RETIRED_FIELD in param
            if not own and inherited is None:
                continue
            value = param[RETIRED_FIELD] if own else inherited
            if "category" in param:
                if own:  # the signal's own category stands; only the old field goes
                    plan.append(Migrated(pid_u, name, value, param["category"], "already set"))
                continue
            unit = effective_param(pdef, param).get("unit")
            category, reason = convert(value, unit)
            if not own and category is None:
                continue  # nothing on the signal to change
            plan.append(Migrated(pid_u, name, value, category, reason))
    return plan


def _rewrite_param(block: str, m: Migrated) -> str:
    """Drop the signal's ``ha_class`` line, writing ``category`` in its place when needed."""
    assert m.param is not None  # the PID-level entry is removed by the caller
    params = _keyed_block(block, "parameters", _PID_INDENT, 0, len(block))
    found = _keyed_block(block, m.param, 8, params[2], params[3]) if params else None
    if not found:
        raise PidsEditError(f"parameter {m.param!r} not found while migrating {RETIRED_FIELD}")
    start, end = found[0], found[3]
    body = block[start:end]
    if m.category is None or m.reason == "already set":
        body = _remove_field_line(body, RETIRED_FIELD, _PARAM_INDENT)
    else:
        line = _format_scalar_field(" " * _PARAM_INDENT, "category", m.category)
        # In the old field's place; a category the PID default supplied goes at the end.
        has_own = f"\n{' ' * _PARAM_INDENT}{RETIRED_FIELD}:" in f"\n{body}"
        field = RETIRED_FIELD if has_own else "category"
        body = _replace_field_in_block_at(body, field, line, _PARAM_INDENT)
    return block[:start] + body + block[end:]


def migrate_categories(
    ecu_name: str, *, dry_run: bool = False, pids_dir: Path | None = None
) -> list[Migrated]:
    """Replace every ``ha_class`` of one ECU with ``category`` (or nothing).

    Returns what changed (or would, with ``dry_run``). The result is checked before it is
    kept: no ``ha_class`` may remain, and every signal must carry exactly the planned
    category.
    """
    fpath = find_ecu_file(ecu_name, pids_dir=pids_dir)
    original = fpath.read_text()
    plan = plan_ecu(_ecu_def(yaml.safe_load(original), ecu_name))
    if dry_run or not plan:
        return plan

    def transform(text: str) -> str:
        for pid_u in dict.fromkeys(m.pid for m in plan):
            start, end = _locate_pid(text, ecu_name, pid_u)
            block = text[start:end]
            for m in (m for m in plan if m.pid == pid_u and m.param is not None):
                block = _rewrite_param(block, m)
            block = _remove_field_line(block, RETIRED_FIELD, _PID_INDENT)
            text = text[:start] + block + text[end:]
        return text

    def checker(new_def: dict) -> None:
        pids = {str(k).upper(): v for k, v in (new_def.get("pids") or {}).items()}
        for m in plan:
            pdef = pids.get(m.pid)
            if not isinstance(pdef, dict) or RETIRED_FIELD in pdef:
                raise PidsEditError(f"{m.pid}: {RETIRED_FIELD} still present after migration")
            if m.param is None:
                continue
            param = _params(pdef).get(m.param, {})
            if RETIRED_FIELD in param or param.get("category") != m.category:
                raise PidsEditError(f"{m.pid}/{m.param}: category not migrated, reverted")

    _safe_write(fpath, original, transform(original), ecu_name.strip().upper(), checker)
    return plan
