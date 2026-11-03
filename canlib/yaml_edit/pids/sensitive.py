"""Editor for the ``sensitive:`` flag on a PID or one of its signals (:mod:`canlib.sensitive`).

Same discipline as the other PID-level editors: a surgical text edit, a YAML re-parse, and a
restore on any failure — the rest of the file is byte-for-byte untouched. ``true`` writes the
field straight after the PID (or signal) header; ``false`` removes it, the default.
"""

from __future__ import annotations

from pathlib import Path

from canlib.yaml_edit.text import (
    PidsEditError,
    _find_ecu_block,
    _keyed_block,
    _remove_field_line,
    _safe_write,
    find_ecu_file,
)

FIELD = "sensitive"


def _inline(block: tuple) -> bool:
    """Is this key's value on its own header line (``{...}``), not a nested block?"""
    return bool(block[4]) and not block[4].startswith("#")


def _set_in_block(block: str, value: bool, indent: int) -> str:
    block = _remove_field_line(block, FIELD, indent=indent)
    if not value:
        return block
    lines = block.splitlines(keepends=True)
    return lines[0] + f"{' ' * indent}{FIELD}: true\n" + "".join(lines[1:])


def set_sensitive(
    ecu_name: str,
    pid: str,
    value: bool,
    *,
    param: str | None = None,
    pids_dir: Path | None = None,
) -> Path:
    """Set (or clear) ``sensitive:`` on a PID, or with ``param`` on one of its signals."""
    fpath = find_ecu_file(ecu_name, pids_dir=pids_dir)
    original = fpath.read_text()
    ecu_key = ecu_name.strip().upper()
    pid_u = str(pid).strip().upper()

    def transform(text: str) -> str:
        ecu_start, ecu_end = _find_ecu_block(text, ecu_name)
        pids = _keyed_block(text, "pids", 2, ecu_start, ecu_end)
        if not pids:
            raise PidsEditError(f"ECU {ecu_name!r} has no pids: section")
        pidb = _keyed_block(text, pid_u, 4, pids[2], pids[3])
        if not pidb:
            raise PidsEditError(f"PID {pid_u!r} not found under {ecu_name!r}")
        if _inline(pidb):
            raise PidsEditError(f"PID {pid_u!r} is a one-line mapping; expand it first")
        start, end, indent = pidb[0], pidb[3], 6
        if param is not None:
            params = _keyed_block(text, "parameters", 6, pidb[2], pidb[3])
            sigb = _keyed_block(text, param, 8, params[2], params[3]) if params else None
            if not sigb:
                raise PidsEditError(f"signal {param!r} not found on {ecu_name} {pid_u}")
            if _inline(sigb):
                raise PidsEditError(f"signal {param!r} is a one-line mapping; expand it first")
            start, end, indent = sigb[0], sigb[3], 10
        return text[:start] + _set_in_block(text[start:end], value, indent) + text[end:]

    def checker(ecu_def: dict) -> None:
        pids_map = ecu_def.get("pids", {}) or {}
        pdef = next((v for k, v in pids_map.items() if str(k).upper() == pid_u), None)
        if pdef is None:
            raise PidsEditError(f"PID {pid_u!r} missing after edit")
        target = pdef if param is None else (pdef.get("parameters") or {}).get(param)
        if not isinstance(target, dict):
            raise PidsEditError(f"signal {param!r} missing after edit")
        if (target.get(FIELD) is True) != value:
            raise PidsEditError(f"{FIELD} mismatch after edit")

    _safe_write(fpath, original, transform(original), ecu_key, checker)
    return fpath
