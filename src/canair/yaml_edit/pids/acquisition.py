"""Edit how a PID is acquired: the states it answers in, monitor cadence and snapshot-only records.

Same discipline as the other PID-level editors: a surgical text edit, a YAML re-parse, and a
restore on any failure — the rest of the file is byte-for-byte untouched.
"""

from __future__ import annotations

import re
from pathlib import Path

from canair.dtc.snapshot import did_of_read
from canair.profile.model import Profile
from canair.yaml_edit.text import (
    PidsEditError,
    _format_inline_list_field,
    _norm_states,
    edit_pid,
    set_scalar,
)

ONLY_FIELD = "snapshot_only"
LENGTH_FIELD = "snapshot_length"


def set_monitor_once(
    ecu_name: str, pid: str, value: bool, *, pids_dir: Path | None = None, profile: Profile
) -> Path:
    """Set or clear the monitor-only one-success-per-run scheduling policy."""

    def edit(block: str) -> str:
        return set_scalar(block, "monitor_once", "monitor_once: true" if value else None)

    def check(pdef: dict) -> None:
        if bool(pdef.get("monitor_once", False)) != value:
            raise PidsEditError("monitor_once mismatch after edit")

    return edit_pid(ecu_name, pid, edit, check, pids_dir=pids_dir, profile=profile)


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


def set_pid_states(
    ecu_name: str,
    pid: str,
    states: list[str] | None,
    *,
    pids_dir: Path | None = None,
    profile: Profile,
) -> Path:
    """Set (or clear) the power states a PID answers in (its ``vehicle_states:``).

    Tokens are normalized to the canonical UPPERCASE form and written as a flow list. The
    post-edit validation checks them against the profile's ``vehicle_states.yaml``. An existing
    one-line list is rewritten in place, so a change diffs as one line. Passing ``None`` removes
    the field (the PID then inherits the ECU's states). Verified by a YAML re-parse, restored on
    failure.
    """
    tokens = None if states is None else _norm_states(states)
    if tokens is not None and not tokens:
        raise PidsEditError("give at least one state, or clear the field")

    def edit(block: str) -> str:
        if tokens is None:
            return set_scalar(block, "vehicle_states", None)
        line = _format_inline_list_field("", "vehicle_states", tokens)[0]
        block, replaced = re.subn(
            r"^( {6})vehicle_states:[ \t]*\[.*\][ \t]*$",
            lambda m: m.group(1) + line,
            block,
            count=1,
            flags=re.MULTILINE,
        )
        return block if replaced else set_scalar(block, "vehicle_states", line)

    def check(pdef: dict) -> None:
        got = pdef.get("vehicle_states")
        if tokens is None:
            if got is not None:
                raise PidsEditError(f"vehicle_states still present after clear: {got!r}")
        elif _norm_states(got) != tokens:
            raise PidsEditError(f"vehicle_states mismatch after edit: {got!r} != {tokens!r}")

    return edit_pid(ecu_name, pid, edit, check, pids_dir=pids_dir, profile=profile)
