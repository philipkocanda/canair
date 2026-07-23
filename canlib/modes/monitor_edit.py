"""In-place PID editing + filtering for the live monitor.

:class:`MonitorEditor` is a focused collaborator of
:class:`canlib.modes.monitor.MonitorController`. It owns the two pieces of state
that turn the read-only monitor into an editor:

- a **selection cursor** over the currently displayed parameter rows, and
- a **display filter** (``all`` / ``verified`` / ``unverified`` / ``enabled`` /
  ``disabled``) that narrows which parameters are shown *and* navigable.

Edits are applied through :func:`canlib.pids_edit.upsert_parameter` (surgical,
comment-preserving, schema-validated) and then the controller reloads its PID
definitions so the next poll decodes with the new expression/flags. The editor
never talks to the CAN bus and never writes capture data — it only mutates the
profile's ``ecus/`` definitions.
"""

from __future__ import annotations

import re
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .monitor import MonitorController

# Ordered display filters cycled by the TUI's filter key.
FILTERS = ("all", "verified", "unverified", "enabled", "disabled")

# A selection cursor identifies one parameter row uniquely across the display.
SelectionKey = tuple[str, str, str]  # (ecu_label, pid, param_name)


def _ecu_name(ecu_label: str) -> str:
    """Leading token of a monitor ECU label (``"BMS (0x7E4)"`` -> ``"BMS"``)."""
    m = re.match(r"(\w+)", ecu_label)
    return m.group(1) if m else ecu_label


class MonitorEditor:
    """Selection, filtering, and in-place parameter editing for the monitor."""

    def __init__(self, controller: MonitorController):
        self.c = controller
        self.selected: SelectionKey | None = None
        self.filter_mode: str = "all"

    # ── filtering ─────────────────────────────────────────────────────────
    def _param_enabled(self, ecu_label: str, pid: str, name: str) -> bool:
        """Param-level ``enabled`` flag (defaults True when undefined)."""
        pdef = self._lookup_pdef(_ecu_name(ecu_label), pid, name)
        return True if pdef is None else bool(pdef.get("enabled", True))

    def _row_matches(self, ecu_label: str, pid: str, row) -> bool:
        name = row[0]
        verified = bool(row[5]) if len(row) > 5 else False
        mode = self.filter_mode
        if mode == "all":
            return True
        if mode == "verified":
            return verified
        if mode == "unverified":
            return not verified
        if mode == "enabled":
            return self._param_enabled(ecu_label, pid, name)
        if mode == "disabled":
            return not self._param_enabled(ecu_label, pid, name)
        return True

    def visible_queries(self, last_queries: list[tuple[str, list]]) -> list[tuple[str, list]]:
        """Return ``last_queries`` narrowed to rows matching the active filter.

        ``all`` returns the input untouched. Any other filter drops parameter
        rows that don't match and drops entries left with no visible parameters
        (raw/unmapped PIDs, which carry no parameter rows, are hidden while a
        filter other than ``all`` is active).
        """
        if self.filter_mode == "all":
            return last_queries
        out: list[tuple[str, list]] = []
        for ecu_label, entries in last_queries:
            filtered_entries = []
            for entry in entries:
                params = entry.get("params") or []
                if not params:
                    continue
                kept = [r for r in params if self._row_matches(ecu_label, entry["pid"], r)]
                if kept:
                    filtered_entries.append({**entry, "params": kept})
            if filtered_entries:
                out.append((ecu_label, filtered_entries))
        return out

    # ── selection ─────────────────────────────────────────────────────────
    def selectable(self, last_queries: list[tuple[str, list]]) -> list[SelectionKey]:
        """Ordered selection keys for every visible, decoded parameter row."""
        items: list[SelectionKey] = []
        for ecu_label, entries in self.visible_queries(last_queries):
            for entry in entries:
                for row in entry.get("params") or []:
                    items.append((ecu_label, entry["pid"], row[0]))
        return items

    def move(self, last_queries: list[tuple[str, list]], delta: int) -> SelectionKey | None:
        """Move the cursor by ``delta`` rows (clamped); return the new selection.

        A first move (or a move after the selected row vanished) snaps to the
        first row for a downward move and the last row for an upward move.
        """
        items = self.selectable(last_queries)
        if not items:
            self.selected = None
            return None
        if self.selected not in items:
            self.selected = items[0] if delta >= 0 else items[-1]
            return self.selected
        idx = items.index(self.selected)
        idx = max(0, min(len(items) - 1, idx + delta))
        self.selected = items[idx]
        return self.selected

    def ensure_valid(self, last_queries: list[tuple[str, list]]) -> None:
        """Drop the selection if it's no longer among the visible rows."""
        if self.selected is not None and self.selected not in self.selectable(last_queries):
            self.selected = None

    def cycle_filter(self, last_queries: list[tuple[str, list]] | None = None) -> str:
        """Advance to the next display filter; keep the selection valid."""
        self.filter_mode = FILTERS[(FILTERS.index(self.filter_mode) + 1) % len(FILTERS)]
        if last_queries is not None:
            self.ensure_valid(last_queries)
        return self.filter_mode

    # ── definition lookup ─────────────────────────────────────────────────
    def _lookup_pdef(self, ecu_name: str, pid: str, name: str) -> dict | None:
        """Current YAML definition for a parameter (case-insensitive ECU/PID)."""
        ecus = self.c.pids_data.get("ecus", {})
        ecu_def = next((v for k, v in ecus.items() if str(k).upper() == ecu_name.upper()), None)
        if not ecu_def:
            return None
        pids = ecu_def.get("pids", {})
        pid_def = next((v for k, v in pids.items() if str(k).upper() == str(pid).upper()), None)
        if not pid_def:
            return None
        return (pid_def.get("parameters") or {}).get(name)

    def edit_target(self) -> dict | None:
        """Field values for the selected parameter, for the edit dialog prefill.

        Returns None when nothing is selected. Keys: ``ecu``, ``pid``, ``name``,
        ``expression``, ``unit``, ``min``, ``max``, ``verified``, ``enabled``,
        ``notes``.
        """
        if self.selected is None:
            return None
        ecu_label, pid, name = self.selected
        ecu = _ecu_name(ecu_label)
        pdef = self._lookup_pdef(ecu, pid, name) or {}
        return {
            "ecu": ecu,
            "pid": pid,
            "name": name,
            "expression": pdef.get("expression", "") or "",
            "unit": pdef.get("unit", "") or "",
            "min": str(pdef.get("min", "") or ""),
            "max": str(pdef.get("max", "") or ""),
            "verified": bool(pdef.get("verified", False)),
            "enabled": bool(pdef.get("enabled", True)),
            "notes": pdef.get("notes", "") or "",
        }

    def selection_label(self) -> str:
        """Short human label for the selected param, e.g. ``BMS 2101 SOC``."""
        if self.selected is None:
            return ""
        ecu_label, pid, name = self.selected
        return f"{_ecu_name(ecu_label)} {pid} {name}"

    # ── mutation ──────────────────────────────────────────────────────────
    def apply_edit(self, fields: dict) -> str:
        """Write the edited fields to the profile and reload PID definitions.

        ``fields`` mirrors :meth:`edit_target` (``expression``/``unit``/``min``/
        ``max``/``verified``/``enabled``/``notes``). Empty ``unit``/``min``/
        ``max``/``notes`` are left unchanged rather than blanked. Returns a
        one-line status message (never raises).
        """
        if self.selected is None:
            return "No parameter selected."
        ecu_label, pid, name = self.selected
        ecu = _ecu_name(ecu_label)
        from ..pids_edit import PidsEditError, upsert_parameter

        def _opt(key):  # keep empty strings from clobbering existing values
            val = fields.get(key)
            return val if val not in (None, "") else None

        try:
            upsert_parameter(
                ecu,
                pid,
                name,
                (fields.get("expression") or "").strip(),
                unit=_opt("unit"),
                min=_opt("min"),
                max=_opt("max"),
                verified=fields.get("verified"),
                enabled=fields.get("enabled"),
                notes=_opt("notes"),
                pids_dir=self.c.pids_dir,
            )
        except PidsEditError as exc:
            return f"Edit failed: {exc}"
        except Exception as exc:  # keep the TUI alive on any unexpected failure
            return f"Edit failed: {exc}"
        self.c.reload_pids()
        return f"Saved {ecu} {pid} {name}"

    def _toggle(self, field: str) -> str:
        """Flip a boolean field (``verified``/``enabled``) on the selected param."""
        if self.selected is None:
            return "No parameter selected."
        ecu_label, pid, name = self.selected
        ecu = _ecu_name(ecu_label)
        pdef = self._lookup_pdef(ecu, pid, name)
        if pdef is None:
            return f"{name}: no definition to edit."
        expression = (pdef.get("expression") or "").strip()
        if not expression:
            return f"{name}: no expression — edit it first."
        default = True if field == "enabled" else False
        new_val = not bool(pdef.get(field, default))
        from ..pids_edit import PidsEditError, upsert_parameter

        # `field` is only ever "verified" or "enabled" (see toggle_* below), both
        # bool params of upsert_parameter. Pass explicitly so the value type is
        # bool (a dynamic **{field: new_val} splat is checked against every kwarg,
        # including the str|None ones, and rejected).
        try:
            if field == "verified":
                upsert_parameter(
                    ecu, pid, name, expression, pids_dir=self.c.pids_dir, verified=new_val
                )
            else:
                upsert_parameter(
                    ecu, pid, name, expression, pids_dir=self.c.pids_dir, enabled=new_val
                )
        except (PidsEditError, Exception) as exc:
            return f"Toggle failed: {exc}"
        self.c.reload_pids()
        return f"{name} {field}={'true' if new_val else 'false'}"

    def toggle_verified(self) -> str:
        return self._toggle("verified")

    def toggle_enabled(self) -> str:
        return self._toggle("enabled")
