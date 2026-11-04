"""Editor for the ``sensitive:`` flag on a PID or one of its signals (:mod:`canair.privacy.sensitive`).

Same discipline as the other PID-level editors: a surgical text edit, a YAML re-parse, and a
restore on any failure — the rest of the file is byte-for-byte untouched. ``true`` writes the
field straight after the PID (or signal) header; ``false`` removes it, the default.
"""

from __future__ import annotations

from pathlib import Path

from canair.yaml_edit.text import (
    Block,
    PidsEditError,
    _keyed_block,
    edit_ecu_text,
    locate_pid,
    pid_checker,
    set_scalar,
)

FIELD = "sensitive"


def _inline(block: Block) -> bool:
    """Is this key's value on its own header line (``{...}``), not a nested block?"""
    return bool(block.inline) and not block.inline.startswith("#")


def set_sensitive(
    ecu_name: str,
    pid: str,
    value: bool,
    *,
    param: str | None = None,
    pids_dir: Path | None = None,
) -> Path:
    """Set (or clear) ``sensitive:`` on a PID, or with ``param`` on one of its signals."""
    pid_u = str(pid).strip().upper()

    def transform(text: str) -> str:
        pidb = locate_pid(text, ecu_name, pid_u)
        if _inline(pidb):
            raise PidsEditError(f"PID {pid_u!r} is a one-line mapping; expand it first")
        start, end, indent = pidb.hdr, pidb.end, 6
        if param is not None:
            params = _keyed_block(text, "parameters", 6, pidb.body, pidb.end)
            sigb = _keyed_block(text, param, 8, params.body, params.end) if params else None
            if not sigb:
                raise PidsEditError(f"signal {param!r} not found on {ecu_name} {pid_u}")
            if _inline(sigb):
                raise PidsEditError(f"signal {param!r} is a one-line mapping; expand it first")
            start, end, indent = sigb.hdr, sigb.end, 10
        block = set_scalar(
            text[start:end], FIELD, f"{FIELD}: true" if value else None, indent=indent
        )
        return text[:start] + block + text[end:]

    def check(pdef: dict) -> None:
        target = pdef if param is None else (pdef.get("parameters") or {}).get(param)
        if not isinstance(target, dict):
            raise PidsEditError(f"signal {param!r} missing after edit")
        if (target.get(FIELD) is True) != value:
            raise PidsEditError(f"{FIELD} mismatch after edit")

    return edit_ecu_text(ecu_name, transform, pid_checker(pid_u, check), pids_dir=pids_dir)
