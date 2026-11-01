"""The shape of an ECU's ``dtcs:`` section: one entry per code, plus fields stated once.

A ``dtcs:`` mapping holds an entry per base DTC code (``C1616``, never ``C1616-08``) and may
also state :data:`SECTION_FIELDS` directly under ``dtcs:``, once for every entry, the way a PID
states fields once for its parameters (:data:`canlib.pids.INHERITED_PARAM_FIELDS`). An entry
that sets such a field itself, even to ``""``/``[]``/``false``, keeps its own value.

The loader resolves the section (:func:`effective_entries`, called from
:func:`canlib.pids.merge_ecu_documents`), so readers of loaded definitions only ever see code
entries. Code that parses the YAML itself must split the two kinds of key with
:func:`split_section`. They cannot be confused: a code is a letter and four hex digits.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from typing import Any

SECTION_FIELDS: tuple[str, ...] = ("source", "source_links", "verified")

_CODE = re.compile(r"[PCBU][0-9A-F]{4}", re.IGNORECASE)


def is_dtc_code(key: object) -> bool:
    """Whether ``key`` is a base DTC code (``B2915``), as opposed to a section field."""
    return bool(_CODE.fullmatch(str(key)))


def split_section(dtcs: Mapping) -> tuple[dict[str, Any], dict[Any, Any]]:
    """``(section fields, entries)`` of a ``dtcs:`` mapping, as written."""
    shared = {str(k): v for k, v in dtcs.items() if str(k) in SECTION_FIELDS}
    entries = {k: v for k, v in dtcs.items() if str(k) not in SECTION_FIELDS}
    return shared, entries


def effective_entries(dtcs: Mapping) -> dict[Any, Any]:
    """The code entries with the section's fields filled in where an entry sets none.

    The section fields themselves are dropped. A bare-string entry (just a description) gets
    nothing; the definition on file is never modified.
    """
    shared, entries = split_section(dtcs)
    shared = {k: v for k, v in shared.items() if v is not None}
    if not shared:
        return entries
    out: dict[Any, Any] = {}
    for code, entry in entries.items():
        if isinstance(entry, Mapping):
            inherited = {f: v for f, v in shared.items() if entry.get(f) is None}
            out[code] = {**entry, **inherited} if inherited else entry
        else:
            out[code] = entry
    return out
