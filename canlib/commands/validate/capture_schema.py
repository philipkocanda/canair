"""The capture-file schema check, with each distinct capture row checked once.

A profile's history holds hundreds of thousands of capture rows, and running
jsonschema over each one was most of ``validate captures``' time. Most rows
differ from an earlier one only in values the schema checks by JSON type alone
(``time``, ``payload``), so a row is keyed with those values replaced by their
type, and a key that already passed the schema is not checked again.

Only a *pass* is remembered. A row that fails is always checked in full, so each
error message quotes that row's own values.
"""

from __future__ import annotations

import copy
from collections.abc import Hashable, Mapping
from typing import Any

from jsonschema import Draft202012Validator, ValidationError

# Keywords that never change whether a value is valid.
_ANNOTATIONS = frozenset({"description", "title", "$comment", "examples"})
# For these values a pure ``type`` keyword's verdict depends on the Python type
# only (unlike a float, which "integer" accepts when it is whole).
_TYPE_DECIDES = (str, bool, type(None))

ErrorAt = tuple[list[Any], ValidationError]


class CaptureSchemaChecker:
    """Validate parsed capture files against ``captures_schema.json``."""

    def __init__(self, schema: Mapping[str, Any] | bool) -> None:
        self._full = Draft202012Validator(schema)
        defs = (schema.get("$defs") if isinstance(schema, Mapping) else None) or {}
        captures = ((defs.get("session") or {}).get("properties") or {}).get("captures") or {}
        self._split = captures.get("items") == {"$ref": "#/$defs/capture"}
        if not isinstance(schema, Mapping) or not self._split:
            # The schema no longer has the layout this check relies on: validate whole files.
            self._split = False
            return
        shell = copy.deepcopy(dict(schema))
        del shell["$defs"]["session"]["properties"]["captures"]["items"]
        self._shell = Draft202012Validator(shell)
        self._row = Draft202012Validator({**defs["capture"], "$defs": defs})
        properties = defs["capture"].get("properties") or {}
        self._type_only = frozenset(
            name
            for name, sub in properties.items()
            if isinstance(sub, dict) and set(sub) - _ANNOTATIONS == {"type"}
        )
        self._passed: set[Hashable] = set()

    def errors(self, data: Any) -> list[ErrorAt]:
        """Every schema error in ``data``, each with its path from the document root."""
        if not self._split:
            return [(list(e.absolute_path), e) for e in self._full.iter_errors(data)]
        out = [(list(e.absolute_path), e) for e in self._shell.iter_errors(data)]
        sessions = data.get("sessions") if isinstance(data, dict) else None
        for si, session in enumerate(sessions if isinstance(sessions, list) else []):
            rows = session.get("captures") if isinstance(session, dict) else None
            for ci, row in enumerate(rows if isinstance(rows, list) else []):
                key = self._key(row)
                if key is not None and key in self._passed:
                    continue
                found = list(self._row.iter_errors(row))
                if not found and key is not None:
                    self._passed.add(key)
                at = ["sessions", si, "captures", ci]
                out.extend((at + list(e.absolute_path), e) for e in found)
        return out

    def _key(self, row: Any) -> Hashable | None:
        """What the schema's verdict on ``row`` depends on, or None if it can't be keyed."""
        if not isinstance(row, dict):
            return None
        parts = []
        for name, value in row.items():
            # The type is in every part, since True == 1 == 1.0 and they hash alike.
            if name in self._type_only and isinstance(value, _TYPE_DECIDES):
                parts.append((name, type(value)))
            else:
                parts.append((name, type(value), value))
        key = tuple(parts)
        try:
            hash(key)
        except TypeError:
            return None  # a nested value (scan_results): check the row every time
        return key
