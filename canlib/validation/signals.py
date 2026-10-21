"""Structural checks for a profile's broadcast signal maps (``signals/<bus>.yaml``).

``canair validate signals`` reports these and :mod:`canlib.signals_edit` uses them
as its rollback guard, so both enforce the same rules. The
``canlib/schema/signals_schema.yaml`` companion documents the same fields for humans.
"""

from __future__ import annotations

import re

# Every field a signal may carry, in canonical render order. Shared by the
# validator and the editor so a field accepted by one is never dropped or rejected
# by the other.
SIGNAL_FIELDS = (
    "start_bit",
    "length",
    "byte_order",
    "signed",
    "scale",
    "offset",
    "min",
    "max",
    "unit",
    "type",
    "values",
    "bits",
    "verified",
    "source",
    "notes",
)
BYTE_ORDERS = ("little", "big")
SIGNAL_TYPES = ("numeric", "enum", "bitmask", "ascii", "date", "bcd")

_ARB_ID_RE = re.compile(r"^0x[0-9A-Fa-f]+$")


def check_signals_doc(data: object) -> tuple[list[str], int]:
    """Structural check of one parsed ``signals/<bus>.yaml`` doc.

    Returns ``(errors, signal_count)``.
    """
    errors: list[str] = []
    if not isinstance(data, dict):
        return (["top level must be a mapping"], 0)
    for extra in set(data) - {"bus", "bitrate", "messages"}:
        errors.append(f"unknown top-level field '{extra}'")
    if "bitrate" in data and not isinstance(data.get("bitrate"), int):
        errors.append("'bitrate' must be an integer")
    messages = data.get("messages") or {}
    if not isinstance(messages, dict):
        return ([*errors, "'messages' must be a mapping keyed by arbitration ID"], 0)
    n_signals = 0
    for mid, msg in messages.items():
        if not _ARB_ID_RE.match(str(mid)):
            errors.append(f"message id '{mid}' is not a hex arbitration ID (e.g. 0x220)")
        if not isinstance(msg, dict):
            errors.append(f"message '{mid}': must be a mapping")
            continue
        for extra in set(msg) - {"name", "tx_ecu", "signals"}:
            errors.append(f"message '{mid}': unknown field '{extra}'")
        signals = msg.get("signals") or {}
        if not isinstance(signals, dict):
            errors.append(f"message '{mid}': 'signals' must be a mapping")
            continue
        for sname, sig in signals.items():
            n_signals += 1
            if not isinstance(sig, dict):
                errors.append(f"{mid}/{sname}: must be a mapping")
                continue
            errors.extend(f"{mid}/{sname}: {e}" for e in _signal_errors(sig))
    return (errors, n_signals)


def _signal_errors(sig: dict) -> list[str]:
    errors = [f"unknown field '{extra}'" for extra in set(sig) - set(SIGNAL_FIELDS)]
    for req in ("start_bit", "length"):
        if req not in sig:
            errors.append(f"missing required '{req}'")
    sb = sig.get("start_bit")
    if isinstance(sb, int) and sb < 0:
        errors.append("start_bit must be >= 0")
    ln = sig.get("length")
    if isinstance(ln, int) and ln < 1:
        errors.append("length must be >= 1")
    bo = sig.get("byte_order")
    if bo is not None and bo not in BYTE_ORDERS:
        errors.append(f"byte_order must be little|big (got '{bo}')")
    if "signed" in sig and not isinstance(sig["signed"], bool):
        errors.append(f"signed must be true|false (got {sig['signed']!r})")
    st = sig.get("type")
    if st is not None and st not in SIGNAL_TYPES:
        errors.append(f"type must be one of {'|'.join(SIGNAL_TYPES)} (got '{st}')")
    return errors
