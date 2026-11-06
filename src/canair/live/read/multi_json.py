"""The ``canair read --json`` document: one entry per PID read, built from the result entries.

``print_ecu_results`` renders the same :class:`~canair.expression.results.ResultEntry` for a
person. A sensitive PID arrives already masked (:func:`canair.privacy.sensitive.mask_result`),
so nothing here re-checks it.
"""

from __future__ import annotations

import math
import re
from typing import Any

from canair.expression.decoding import ParamRow
from canair.expression.results import ResultEntry
from canair.privacy.sensitive import MASK, request_hidden
from canair.profile import Profile
from canair.uds.response import UdsResponse


def _ecu_name(ecu_label: str) -> str:
    """``BMS (0x7E4)`` -> ``BMS``."""
    return re.sub(r"\s*\(0x[0-9A-Fa-f]+\)$", "", ecu_label)


def _number(value: Any) -> Any:
    """A JSON-safe value: NaN and infinity are not JSON, so they become null."""
    if isinstance(value, float) and not math.isfinite(value):
        return None
    return value


def _param(row: ParamRow) -> dict[str, Any]:
    name, value, unit, _expression, error, verified = row[:6]
    doc: dict[str, Any] = {
        "name": name,
        "value": _number(value),
        "unit": unit,
        "verified": bool(verified),
    }
    if len(row) > 6 and row[6]:
        doc["display"] = row[6]
    if error:
        doc["error"] = error
    return doc


def query_documents(ecu_label: str, pid_results: list[ResultEntry]) -> list[dict[str, Any]]:
    """One entry per PID of a query step: ``ecu``, ``pid``, ``raw`` hex, ``params``, ``error``."""
    ecu = _ecu_name(ecu_label)
    docs: list[dict[str, Any]] = []
    for entry in pid_results:
        doc: dict[str, Any] = {
            "ecu": ecu,
            "pid": entry["pid"],
            "raw": entry.get("raw_hex") or None,
            "params": [_param(row) for row in entry.get("params", [])],
        }
        if entry.get("error"):
            doc["error"] = entry["error"]
        docs.append(doc)
    return docs


def raw_document(
    tx_id: int, request: str, response: UdsResponse, *, profile: Profile
) -> dict[str, Any]:
    """The entry for a ``raw`` step: the request and the reply (or why there was none)."""
    shown = MASK if request_hidden(tx_id, request, profile=profile) else response.get("hex")
    doc: dict[str, Any] = {
        "ecu": f"0x{tx_id:03X}",
        "pid": request,
        "raw": shown if response.get("ok") else None,
        "params": [],
    }
    if not response.get("ok"):
        doc["error"] = response.get("error") or response.get("nrc_desc") or "no reply"
    return doc
