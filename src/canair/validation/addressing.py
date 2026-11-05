"""Addressing checks: the ``addressing:`` blocks, and warnings for the ELM327 transports.

The ECU file's block validator (:func:`validate_ecu_addressing`) is at the end;
``profile_schema.json`` checks the ``profile.yaml`` block. The rest is the warnings for
the ELM327 transports and AutoPID.

canair sets each ECU's header, flow control and receive filter itself, from the
profile's ``addressing:`` and the per-ECU ``rx_id``/``fc_id`` (see
:mod:`canair.transport.elm327.addressing`). These checks catch a profile that works
against that: an ``init:`` string that sets the same things session-wide, a
functional-request ECU with no physical flow-control id, ECUs that share a request id
but not an address, and an init protocol whose
CAN id width does not match the ECUs'. Warnings, not errors: each only affects some
transports, and an unusual adapter may need an unusual init.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from typing import Any

from canair.transport.elm327.addressing import is_functional_id
from canair.uds.addressing import (
    AddressingMode,
    is_extended,
    resolve_ecu_address,
    resolve_mode,
    shared_tx_ids,
)

from ._common import is_int

# Modes in which several ECUs share one request id by design.
_EXTENDED_ADDRESSING_MODES = frozenset(
    {AddressingMode.NORMAL_EXTENDED_11BIT, AddressingMode.EXTENDED_29BIT}
)

# Init commands that set what canair now sets per ECU. A receive filter (ATCRA) is
# deliberately not here: the one an init leaves in place is the baseline every
# conventional ECU keeps, which is how the ELM327-Emulator fixture filters to one ECU.
_MANAGED_PREFIXES = ("ATSH", "ATFCSH", "ATFCSD", "ATFCSM")

# ELM327 CAN protocols by id width (6/8 are 11-bit, 7/9 are 29-bit), selected by
# `AT SP` or `AT TP`, either with the auto-search `A` before or after the digit.
_11BIT_PROTOCOLS = frozenset("68")
_29BIT_PROTOCOLS = frozenset("79")
_PROTOCOL_RE = re.compile(r"^AT[ST]P(?:A([0-9A-C])|([0-9A-C])A?)$")


def _init_commands(init: str) -> list[str]:
    return [c.strip().upper().replace(" ", "") for c in init.split(";") if c.strip()]


def _init_protocol(commands: list[str]) -> str | None:
    """The protocol digit the init finally selects, if it selects one."""
    protocol = None
    for cmd in commands:
        match = _PROTOCOL_RE.match(cmd)
        if match:
            protocol = match.group(1) or match.group(2)
    return protocol


def _is_id(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def _addressable_ecus(pids_data: Mapping[str, Any]) -> dict[str, Mapping[str, Any]]:
    """ECUs whose ids are well-formed; a malformed one is the per-file checks' error."""
    return {
        str(name): ecu_def
        for name, ecu_def in (pids_data.get("ecus") or {}).items()
        if isinstance(ecu_def, Mapping)
        and _is_id(ecu_def.get("tx_id"))
        and (ecu_def.get("rx_id") is None or _is_id(ecu_def.get("rx_id")))
    }


def _shared_id_warnings(pids_data: Mapping[str, Any]) -> list[str]:
    """ECUs that share one request id but differ in addressing, outside extended modes.

    The extended-addressing modes share a request id by design (BMW ``0x6F1``) and
    are told apart by a payload byte, so they are not reported.
    """
    warnings: list[str] = []
    ecus = pids_data.get("ecus") or {}
    for tx_id, names in sorted(shared_tx_ids(pids_data).items()):
        modes = {resolve_mode(pids_data, ecus[name]) for name in names}
        if modes <= _EXTENDED_ADDRESSING_MODES:
            continue
        warnings.append(
            f"{', '.join(names)}: share tx_id 0x{tx_id:X} with different addressing. The "
            "ELM327 transports are addressed by request id alone, so they treat it as a "
            "broadcast (the adapter's own receive filter and flow control) and any of "
            "these ECUs may answer; slcan-tcp uses the last one's addressing for all of "
            "them. AutoPID addresses each PID from its own ECU"
        )
    return warnings


def addressing_warnings(pids_data: Mapping[str, Any]) -> list[str]:
    """Warnings for loaded PID data (``profile.yaml`` merged with ``ecus/``)."""
    warnings: list[str] = []
    init = pids_data.get("init")
    commands = _init_commands(init) if isinstance(init, str) else []

    for cmd in commands:
        if cmd.startswith(_MANAGED_PREFIXES):
            warnings.append(
                f"profile.yaml: init sets {cmd}; canair sets the header and flow control "
                "per ECU from addressing:/rx_id/fc_id, so this has no effect or applies "
                "to the wrong ECU — remove it"
            )

    ecus = _addressable_ecus(pids_data)
    data = {**pids_data, "ecus": ecus}
    warnings.extend(_shared_id_warnings(data))

    protocol = _init_protocol(commands)
    for ecu_name, ecu_def in ecus.items():
        address = resolve_ecu_address(pids_data, ecu_def)
        if is_functional_id(address.tx_id) and address.fc_id is None:
            warnings.append(
                f"{ecu_name}: tx_id 0x{address.tx_id:X} is a functional broadcast id with no "
                "addressing.fc_id, so it is addressed as a broadcast: slcan-tcp sends flow "
                "control to the broadcast id, and the ELM327 transports keep the adapter's "
                "own receive filter (which follows the header: 0x7DF admits the "
                "0x7E8-0x7EF replies) and flow control. "
                "If the ECU answers on one physical id, set rx_id and fc_id with "
                "`canair pids set-addressing`"
            )
        if protocol is None:
            continue
        extended = is_extended(address.mode)
        if extended and protocol in _11BIT_PROTOCOLS:
            width, wanted = "29-bit", "protocol 7 or 9 (ATTP7/ATTP9)"
        elif not extended and protocol in _29BIT_PROTOCOLS:
            width, wanted = "11-bit", "protocol 6 or 8 (ATTP6/ATTP8)"
        else:
            continue
        warnings.append(
            f"{ecu_name}: {width} addressing ({address.mode}) but init selects protocol "
            f"{protocol}; the ELM327 transports need {wanted} for it"
        )
    return warnings


# Accepted keys in a per-ECU `addressing:` override block: the profile-level fields
# (profile_schema.json) plus fc_id, a flow-control arbitration override, minus rx_offset
# (a per-ECU rx_id is the per-ECU response override).
_ECU_ADDRESSING_FIELDS: set[str] = {"mode", "target_address", "source_address", "fc_id"}


def _valid_addressing_modes() -> set[str]:
    """The addressing-mode vocabulary, sourced from canair.uds.addressing (no drift)."""
    return {m.value for m in AddressingMode}


def _validate_addressing_byte(block: dict, key: str, label: str, errors: list[str]) -> None:
    """A shared 0–255 byte check for an addressing extension field (target/source)."""
    if key in block and not (is_int(block[key]) and 0 <= block[key] <= 0xFF):
        errors.append(f"{label}: 'addressing.{key}' must be a byte (0–255)")


def validate_ecu_addressing(addressing: Any, label: str, errors: list[str]) -> None:
    """Validate a per-ECU ``addressing:`` override block.

    Accepts ``mode``, the ISO-TP extension bytes ``target_address``/
    ``source_address`` (extended 11-bit/29-bit modes), and a flow-control
    arbitration override ``fc_id`` (functional-TX / physical-RX ECUs).
    """
    if addressing is None:
        return
    if not isinstance(addressing, dict):
        errors.append(f"{label}: 'addressing' must be a mapping")
        return

    for key in addressing:
        if key not in _ECU_ADDRESSING_FIELDS:
            errors.append(
                f"{label}: unknown addressing field '{key}' "
                f"(allowed: {', '.join(sorted(_ECU_ADDRESSING_FIELDS))})"
            )
    if "mode" in addressing:
        modes = _valid_addressing_modes()
        if addressing["mode"] not in modes:
            errors.append(
                f"{label}: 'addressing.mode' must be one of "
                f"{', '.join(sorted(modes))}, got {addressing['mode']!r}"
            )
    _validate_addressing_byte(addressing, "target_address", label, errors)
    _validate_addressing_byte(addressing, "source_address", label, errors)
    if "fc_id" in addressing and not (is_int(addressing["fc_id"]) and addressing["fc_id"] > 0):
        errors.append(f"{label}: 'addressing.fc_id' must be a positive CAN arbitration id")
