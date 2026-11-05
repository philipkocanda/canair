"""The per-ECU ``isotp:`` block: one ECU's own ISO-TP flow control.

Which fields it may hold is :data:`canair.transport.isotp_params.ECU_ISOTP_FIELDS`, the one
list the transports and this check share. The profile-wide ``isotp:`` block in ``profile.yaml``
is checked by its JSON Schema instead.
"""

from __future__ import annotations

from typing import Any

from canair.transport.isotp_params import ECU_ISOTP_FIELDS

from ._common import is_int

# The FlowControl frame carries BlockSize in one byte.
_MAX_BLOCKSIZE = 0xFF


def validate_ecu_isotp(block: Any, label: str, errors: list[str]) -> None:
    """Append an error for each problem in an ECU's ``isotp:`` block (None: no block)."""
    if block is None:
        return
    if not isinstance(block, dict) or not block:
        errors.append(f"{label}: 'isotp' must be a non-empty mapping")
        return
    for key, value in block.items():
        if key not in ECU_ISOTP_FIELDS:
            errors.append(
                f"{label}: 'isotp.{key}' cannot be set per ECU (allowed: "
                f"{', '.join(ECU_ISOTP_FIELDS)}; set the rest in profile.yaml)"
            )
        elif not (is_int(value) and value >= 0):
            errors.append(f"{label}: 'isotp.{key}' must be a non-negative integer")
        elif key == "blocksize" and value > _MAX_BLOCKSIZE:
            errors.append(f"{label}: 'isotp.blocksize' must be at most {_MAX_BLOCKSIZE}")
