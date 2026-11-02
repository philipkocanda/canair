"""Editor for a PID's ``snapshot_only:`` flag and ``snapshot_length:`` (see :mod:`canlib.dtc.snapshot`).

Same discipline as the other PID-level editors: a surgical text edit, a YAML re-parse, and a
restore on any failure — the rest of the file is byte-for-byte untouched.
"""

from __future__ import annotations

from pathlib import Path

from canlib.dtc.snapshot import did_of_read

from ._text import (
    PidsEditError,
    _find_ecu_block,
    _keyed_block,
    _remove_field_line,
    _safe_write,
    find_ecu_file,
)

ONLY_FIELD = "snapshot_only"
LENGTH_FIELD = "snapshot_length"


def set_snapshot_only(
    ecu_name: str,
    pid: str,
    value: bool,
    *,
    length: int | None = None,
    pids_dir: Path | None = None,
) -> Path:
    """Mark a PID as served only inside a DTC snapshot, or (``value=False``) clear the mark.

    ``length`` writes ``snapshot_length:``, the DID's data length in a snapshot record; it
    is kept when omitted, and clearing the mark removes it too. Both fields go straight
    after the PID header. Only a service-22 DID can be snapshot-only. The result is
    re-parsed and must read back as asked; on failure the original file is restored.
    """
    pid_u = str(pid).strip().upper()
    if did_of_read(pid_u) is None:
        raise PidsEditError(
            f"{pid_u!r} is not a service-22 DID (22 + 4 hex digits): a DTC snapshot records "
            "data identifiers"
        )
    if length is not None and length < 1:
        raise PidsEditError(f"snapshot length must be >= 1, got {length}")
    if length is not None and not value:
        raise PidsEditError("a snapshot length only goes with snapshot_only")

    fpath = find_ecu_file(ecu_name, pids_dir=pids_dir)
    original = fpath.read_text()
    ecu_key = ecu_name.strip().upper()
    kept_length: list[int | None] = [None]

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
        block = text[p_hdr:p_body_end]
        kept_length[0] = length if length is not None else _current_length(block)
        block = _remove_field_line(block, ONLY_FIELD, indent=6)
        block = _remove_field_line(block, LENGTH_FIELD, indent=6)
        if value:
            fields = f"      {ONLY_FIELD}: true\n"
            if kept_length[0] is not None:
                fields += f"      {LENGTH_FIELD}: {kept_length[0]}\n"
            lines = block.splitlines(keepends=True)
            block = lines[0] + fields + "".join(lines[1:])
        return text[:p_hdr] + block + text[p_body_end:]

    def checker(ecu_def: dict) -> None:
        pids_map = ecu_def.get("pids", {}) or {}
        pdef = next((v for k, v in pids_map.items() if str(k).upper() == pid_u), None)
        if pdef is None:
            raise PidsEditError(f"PID {pid_u!r} missing after edit")
        got = (pdef.get(ONLY_FIELD) is True, pdef.get(LENGTH_FIELD))
        want = (value, kept_length[0] if value else None)
        if got != want:
            raise PidsEditError(f"snapshot fields mismatch after edit: {got!r} != {want!r}")

    _safe_write(fpath, original, transform(original), ecu_key, checker)
    return fpath


def _current_length(block: str) -> int | None:
    """The ``snapshot_length:`` already on a PID block, so a bare re-mark keeps it."""
    prefix = f"      {LENGTH_FIELD}:"
    for line in block.splitlines():
        if line.startswith(prefix):
            try:
                return int(line[len(prefix) :].split("#", 1)[0].strip())
            except ValueError:
                return None
    return None
