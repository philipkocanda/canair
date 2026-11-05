"""Checks on memory reads: an ECU's ``memory:`` section and its memory PIDs.

A memory PID is one whose key is a ReadMemoryByAddress request (``23…``). Beyond the
usual PID checks it must:

* be a request canair can read back to an address and a size, in the ECU's format;
* read at most :data:`~canair.uds.memory.MEMORY_SIGNAL_MAX_BYTES` (a larger read is a
  dump, which never enters a profile) — an error;
* never ship to the device: ``ship: true`` on one of its signals is an error (``send_to``
  is refused by :mod:`canair.validation.send_to`);
* be gated to a revision with a ``software:`` fingerprint, since a reflash moves what an
  address holds — a warning, as a profile may know one build only.
"""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path
from typing import Any

from canair.ecus.memory import SECTION, memory_format_of, protocol_of, section_problems
from canair.uds.memory import MEMORY_SIGNAL_MAX_BYTES, is_memory_request, parse_memory_request
from canair.variants.revisions import gate, revision_entry


def _software_gated(ecu_def: Mapping, pid_def: Mapping) -> bool | None:
    """Whether the PID's gate names a revision with a software fingerprint (None: no gate)."""
    revs = gate(pid_def)
    if not revs:
        return None
    for rid in revs:
        fps = revision_entry(ecu_def, rid).get("fingerprints")
        if isinstance(fps, list) and any(isinstance(f, Mapping) and "software" in f for f in fps):
            return True
    return False


def _pid_problems(ecu_def: Mapping, pid_code: Any, pid_def: Any) -> tuple[list[str], list[str]]:
    errors: list[str] = []
    warnings: list[str] = []
    read = parse_memory_request(pid_code, protocol_of(ecu_def))
    if read is None:
        return [
            "PID key is a 23 request canair cannot read as a memory read of this ECU's "
            "protocol (write it as a 23/ADDRESS+SIZE token through `canair pids`)"
        ], []
    fmt = memory_format_of(ecu_def)
    if fmt is not None and read.format != fmt:
        errors.append(f"PID key uses memory format {read.format.name}, but the ECU's is {fmt.name}")
    if read.size > MEMORY_SIGNAL_MAX_BYTES:
        errors.append(
            f"reads {read.size} bytes; a memory PID reads at most {MEMORY_SIGNAL_MAX_BYTES} "
            "(read more with `canair memory dump`, which keeps it off the profile)"
        )
    if not isinstance(pid_def, Mapping):
        return errors, warnings
    for name, param in (pid_def.get("parameters") or {}).items():
        if isinstance(param, Mapping) and param.get("ship") is True:
            errors.append(f"signal {name}: ship: true on a memory read — memory PIDs never ship")
    gated = _software_gated(ecu_def, pid_def)
    if gated is None:
        warnings.append(
            "memory PID is not gated to a revision: an address belongs to one software "
            "build, so gate it (`canair pids set-revisions`) to a revision with a "
            "software: fingerprint"
        )
    elif not gated:
        warnings.append(
            "memory PID's revisions have no software: fingerprint, so a reflash that moves "
            "the address cannot be told apart"
        )
    return errors, warnings


def validate_memory(
    ecu_def: Mapping, path: Path, ecu_name: str, errors: list[str], warnings: list[str]
) -> None:
    """Check the ECU's ``memory:`` section and every memory PID it defines."""
    label = f"{path.name}/{ecu_name}"
    if SECTION in ecu_def:
        errs, warns = section_problems(ecu_def[SECTION])
        errors.extend(f"{label}: {e}" for e in errs)
        warnings.extend(f"{label}: {w}" for w in warns)
    pids = ecu_def.get("pids")
    if not isinstance(pids, Mapping):
        return
    for pid_code, pid_def in pids.items():
        if not is_memory_request(pid_code):
            continue
        errs, warns = _pid_problems(ecu_def, pid_code, pid_def)
        errors.extend(f"{label}/{pid_code}: {e}" for e in errs)
        warnings.extend(f"{label}/{pid_code}: {w}" for w in warns)
