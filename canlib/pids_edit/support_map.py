"""Editor for a PID's ``support_map:`` declaration (see :mod:`canlib.uds.support_map`).

Same discipline as the other PID-level editors: a surgical text edit, a YAML re-parse, and a
restore on any failure — the rest of the file is byte-for-byte untouched.
"""

from __future__ import annotations

from pathlib import Path

from canlib.uds.support_map import MapDecl, SupportMapError, declaration

from ._text import (
    PidsEditError,
    _find_ecu_block,
    _keyed_block,
    _remove_field_line,
    _safe_write,
    find_ecu_file,
)

FIELD = "support_map"


def set_support_map(
    ecu_name: str, pid: str, decl: MapDecl | None, *, pids_dir: Path | None = None
) -> Path:
    """Set (or, with ``None``, clear) a PID's ``support_map:`` as a one-line flow mapping.

    Any existing declaration is dropped and the new one written straight after the PID header,
    like the other PID-level scalars. The result is re-parsed and must read back as ``decl``;
    on failure the original file is restored.
    """
    fpath = find_ecu_file(ecu_name, pids_dir=pids_dir)
    original = fpath.read_text()
    ecu_key = ecu_name.strip().upper()
    pid_u = str(pid).strip().upper()

    def transform(text: str) -> str:
        ecu_start, ecu_end = _find_ecu_block(text, ecu_name)
        pids = _keyed_block(text, "pids", 2, ecu_start, ecu_end)
        if not pids:
            raise PidsEditError(f"ECU {ecu_name!r} has no pids: section")
        _, _, pids_body_start, pids_body_end, _ = pids
        pidb = _keyed_block(text, pid_u, 4, pids_body_start, pids_body_end)
        if not pidb:
            raise PidsEditError(f"PID {pid_u!r} not found under {ecu_name!r}")
        p_hdr, _line_end, _body_start, p_body_end, _inline = pidb
        block = _remove_field_line(text[p_hdr:p_body_end], FIELD, indent=6)
        if decl is not None:
            lines = block.splitlines(keepends=True)
            block = lines[0] + f"      {FIELD}: {decl.flow()}\n" + "".join(lines[1:])
        return text[:p_hdr] + block + text[p_body_end:]

    def checker(ecu_def: dict) -> None:
        pids_map = ecu_def.get("pids", {}) or {}
        pdef = next((v for k, v in pids_map.items() if str(k).upper() == pid_u), None)
        if pdef is None:
            raise PidsEditError(f"PID {pid_u!r} missing after edit")
        try:
            got = declaration(pdef)
        except SupportMapError as e:
            raise PidsEditError(str(e)) from e
        if got != decl:
            raise PidsEditError(f"support_map mismatch after edit: {got!r} != {decl!r}")

    _safe_write(fpath, original, transform(original), ecu_key, checker)
    return fpath
