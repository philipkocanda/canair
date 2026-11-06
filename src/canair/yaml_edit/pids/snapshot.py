"""Editor for a PID's ``snapshot_only:`` flag and ``snapshot_length:`` (see :mod:`canair.dtc.snapshot`).

Same discipline as the other PID-level editors: a surgical text edit, a YAML re-parse, and a
restore on any failure — the rest of the file is byte-for-byte untouched.
"""

from __future__ import annotations

from pathlib import Path

from canair.dtc.snapshot import did_of_read
from canair.profile.model import Profile
from canair.yaml_edit.text import PidsEditError, edit_pid, set_scalar

ONLY_FIELD = "snapshot_only"
LENGTH_FIELD = "snapshot_length"


def set_snapshot_only(
    ecu_name: str,
    pid: str,
    value: bool,
    *,
    length: int | None = None,
    pids_dir: Path | None = None,
    profile: Profile,
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

    kept_length: list[int | None] = [None]

    def edit(block: str) -> str:
        kept_length[0] = length if length is not None else _current_length(block)
        block = set_scalar(block, ONLY_FIELD, None)
        block = set_scalar(block, LENGTH_FIELD, None)
        if not value:
            return block
        if kept_length[0] is not None:
            block = set_scalar(block, LENGTH_FIELD, f"{LENGTH_FIELD}: {kept_length[0]}")
        return set_scalar(block, ONLY_FIELD, f"{ONLY_FIELD}: true")

    def check(pdef: dict) -> None:
        got = (pdef.get(ONLY_FIELD) is True, pdef.get(LENGTH_FIELD))
        want = (value, kept_length[0] if value else None)
        if got != want:
            raise PidsEditError(f"snapshot fields mismatch after edit: {got!r} != {want!r}")

    return edit_pid(ecu_name, pid_u, edit, check, pids_dir=pids_dir, profile=profile)


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
