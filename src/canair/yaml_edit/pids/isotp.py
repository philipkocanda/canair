"""Editor for an ECU's own ``isotp:`` block (:data:`canair.transport.isotp_params.ECU_ISOTP_FIELDS`).

Same discipline as the other ECU-level text editors: a surgical edit of that one block, a YAML
re-parse and a restore on any failure, so the rest of the file stays byte-for-byte the same. A
value of ``None`` removes that field; removing the last one removes the block, so the ECU falls
back to the profile's ``isotp:`` block.
"""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path
from typing import Any

import yaml

from canair.profile.model import Profile
from canair.transport.isotp_params import ECU_ISOTP_FIELDS
from canair.yaml_edit.text import (
    PidsEditError,
    _ecu_def_of,
    _find_ecu_block,
    _keyed_block,
    _remove_field_line,
    _replace_field_in_block_at,
    edit_ecu_text,
)

FIELD = "isotp"

# Where a new block goes: after the last of these the ECU has, with the other transport keys.
_AFTER = ("addressing", "rx_id", "tx_id")


def _current(text: str, ecu_name: str) -> dict[str, Any]:
    """The ECU's ``isotp:`` block as it stands (empty when it has none)."""
    data = yaml.safe_load(text)
    block = _ecu_def_of(data if isinstance(data, dict) else {}, ecu_name).get(FIELD)
    return dict(block) if isinstance(block, Mapping) else {}


def set_ecu_isotp(
    ecu_name: str,
    values: Mapping[str, int | None],
    *,
    pids_dir: Path | None = None,
    profile: Profile,
) -> Path:
    """Set (an int) or remove (``None``) fields of an ECU's own ``isotp:`` block."""
    unknown = [k for k in values if k not in ECU_ISOTP_FIELDS]
    if unknown:
        raise PidsEditError(
            f"isotp.{unknown[0]} cannot be set per ECU (allowed: {', '.join(ECU_ISOTP_FIELDS)})"
        )
    if not values:
        raise PidsEditError("nothing to set: give at least one isotp field")
    # The block the edit must leave, worked out from the file it reads and judged by check().
    want: dict[str, int] = {}

    def transform(text: str) -> str:
        merged: dict[str, Any] = {**_current(text, ecu_name), **values}
        want.clear()
        want.update({k: int(merged[k]) for k in ECU_ISOTP_FIELDS if merged.get(k) is not None})
        lines = [f"  {FIELD}:"] + [f"    {k}: {v}" for k, v in want.items()]
        start, end = _find_ecu_block(text, ecu_name)
        body = text.find("\n", start) + 1
        if _keyed_block(text, FIELD, 2, body, end):
            block = text[body:end]
            if want:
                new = _replace_field_in_block_at(block, FIELD, lines, indent=2)
            else:
                new = _remove_field_line(block, FIELD, 2)
            return text[:body] + new + text[end:]
        if not want:
            return text
        at = body
        for key in _AFTER:
            anchor = _keyed_block(text, key, 2, body, end)
            if anchor:
                at = anchor.end if key == "addressing" else anchor.line_end + 1
                break
        return text[:at] + "".join(ln + "\n" for ln in lines) + text[at:]

    def check(ecu_def: dict) -> None:
        got = ecu_def.get(FIELD)
        if (dict(got) if isinstance(got, Mapping) else {}) != want:
            raise PidsEditError(f"{FIELD} mismatch after edit (got {got!r})")

    return edit_ecu_text(ecu_name, transform, check, pids_dir=pids_dir, profile=profile)
