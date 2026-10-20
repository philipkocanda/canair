"""Addressing warnings for the ELM327 transports and AutoPID.

canair sets each ECU's header, flow control and receive filter itself, from the
profile's ``addressing:`` and the per-ECU ``rx_id``/``fc_id`` (see
:mod:`canlib.transport.elm327_addressing`). These checks catch a profile that works
against that: an ``init:`` string that sets the same things session-wide, a
functional-request ECU with no physical flow-control id, and an init protocol whose
CAN id width does not match the ECUs'. Warnings, not errors: each only affects some
transports, and an unusual adapter may need an unusual init.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from typing import Any

from canlib.addressing import is_extended, resolve_ecu_address
from canlib.transport.elm327_addressing import is_functional_id

# Init commands that set what canair now sets per ECU. A receive filter (ATCRA) is
# deliberately not here: the one an init leaves in place is the baseline every
# conventional ECU keeps, which is how the ELM327-Emulator fixture filters to one ECU.
_MANAGED_PREFIXES = ("ATSH", "ATFCSH", "ATFCSD", "ATFCSM")

# ELM327 CAN protocols by id width (ATSP6/8 are 11-bit, 7/9 are 29-bit).
_11BIT_PROTOCOLS = frozenset("68")
_29BIT_PROTOCOLS = frozenset("79")
_PROTOCOL_RE = re.compile(r"^ATSPA?([0-9A-C])$")


def _init_commands(init: str) -> list[str]:
    return [c.strip().upper().replace(" ", "") for c in init.split(";") if c.strip()]


def _init_protocol(commands: list[str]) -> str | None:
    """The protocol digit the init finally selects, if it selects one."""
    protocol = None
    for cmd in commands:
        match = _PROTOCOL_RE.match(cmd)
        if match:
            protocol = match.group(1)
    return protocol


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

    protocol = _init_protocol(commands)
    for ecu_name, ecu_def in (pids_data.get("ecus") or {}).items():
        if not isinstance(ecu_def, Mapping) or "tx_id" not in ecu_def:
            continue
        address = resolve_ecu_address(pids_data, ecu_def)
        if is_functional_id(address.tx_id) and address.fc_id is None:
            warnings.append(
                f"{ecu_name}: tx_id 0x{address.tx_id:X} is a functional broadcast id with no "
                "addressing.fc_id, so it is addressed as a broadcast: slcan-tcp sends flow "
                "control to the broadcast id, and the ELM327 transports accept any reply. "
                "If the ECU answers on one physical id, set rx_id and fc_id with "
                "`canair pids set-addressing`"
            )
        if protocol is None:
            continue
        extended = is_extended(address.mode)
        if extended and protocol in _11BIT_PROTOCOLS:
            width, wanted = "29-bit", "ATSP7/ATSP9"
        elif not extended and protocol in _29BIT_PROTOCOLS:
            width, wanted = "11-bit", "ATSP6/ATSP8"
        else:
            continue
        warnings.append(
            f"{ecu_name}: {width} addressing ({address.mode}) but init selects ATSP{protocol}; "
            f"the ELM327 transports need {wanted} for it"
        )
    return warnings
