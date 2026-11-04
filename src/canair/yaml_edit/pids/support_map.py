"""Editor for a PID's ``support_map:`` declaration (see :mod:`canair.uds.support_map`).

Same discipline as the other PID-level editors: a surgical text edit, a YAML re-parse, and a
restore on any failure — the rest of the file is byte-for-byte untouched.
"""

from __future__ import annotations

from pathlib import Path

from canair.uds.support_map import MapDecl, SupportMapError, declaration
from canair.yaml_edit.text import PidsEditError, edit_pid, set_scalar

FIELD = "support_map"


def set_support_map(
    ecu_name: str, pid: str, decl: MapDecl | None, *, pids_dir: Path | None = None
) -> Path:
    """Set (or, with ``None``, clear) a PID's ``support_map:`` as a one-line flow mapping.

    Any existing declaration is dropped and the new one written straight after the PID header,
    like the other PID-level scalars. The result is re-parsed and must read back as ``decl``;
    on failure the original file is restored.
    """

    def check(pdef: dict) -> None:
        try:
            got = declaration(pdef)
        except SupportMapError as e:
            raise PidsEditError(str(e)) from e
        if got != decl:
            raise PidsEditError(f"support_map mismatch after edit: {got!r} != {decl!r}")

    return edit_pid(
        ecu_name,
        pid,
        lambda block: set_scalar(block, FIELD, None if decl is None else f"{FIELD}: {decl.flow()}"),
        check,
        pids_dir=pids_dir,
    )
