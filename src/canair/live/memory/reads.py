"""The ECU a memory command targets, one checked memory read, and the session it needs."""

from __future__ import annotations

from collections.abc import AsyncGenerator, Mapping
from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import Any

from canair.ecus.memory import EcuMemory, ecu_memory, protocol_of
from canair.transport.protocol import Terminal
from canair.transport.session_manager import SessionManager
from canair.uds.memory import MemoryFormat, MemoryRead, RegionResult, classify_memory_reply
from canair.uds.protocol import DiagProtocol
from canair.uds.response import UdsResponse
from canair.uds.services import READ_MEMORY_BY_ADDRESS

#: A memory read's reply budget. A large block takes many frames on a slow link.
READ_TIMEOUT_S = 3.0


class MemoryTargetError(ValueError):
    """The ECU cannot be found, or has no memory format to read with."""


@dataclass(frozen=True)
class MemoryTarget:
    """The ECU a memory command reads: its name, address, protocol and memory section."""

    name: str
    tx_id: int
    protocol: DiagProtocol
    memory: EcuMemory | None
    ecu_def: Mapping[str, Any]

    @property
    def format(self) -> MemoryFormat | None:
        return self.memory.format if self.memory is not None else None


def resolve_target(pids_data: Mapping[str, Any], ecu: str) -> MemoryTarget:
    """The profile ECU named ``ecu`` (name, alias-free, or hex TX id)."""
    wanted = ecu.strip().upper().removeprefix("0X")
    for name, ecu_def in (pids_data.get("ecus") or {}).items():
        if not isinstance(ecu_def, Mapping) or ecu_def.get("tx_id") is None:
            continue
        tx = int(ecu_def["tx_id"])
        if name.upper() == wanted or f"{tx:X}" == wanted:
            return MemoryTarget(name, tx, protocol_of(ecu_def), ecu_memory(ecu_def), ecu_def)
    raise MemoryTargetError(f"ECU {ecu!r} is not in the profile")


async def read_memory(terminal: Terminal, tx_id: int, read: MemoryRead) -> UdsResponse:
    """Send one memory read to ``tx_id``; the reply is SID- and length-checked by the parser."""
    async with terminal.transaction():
        await terminal.set_header(tx_id)
        return await terminal.send_uds(
            read.request,
            timeout=READ_TIMEOUT_S,
            expected_sid=READ_MEMORY_BY_ADDRESS,
            expected_echo=b"",
        )


def classify(response: UdsResponse) -> RegionResult:
    """What a reply means; a reply that failed its checks counts as no answer."""
    return classify_memory_reply(response)


def data_of(response: UdsResponse) -> bytes:
    """The memory bytes of a positive reply (everything after the ``63``)."""
    return bytes(response.get("bytes") or b"")[1:]


@asynccontextmanager
async def maybe_session(terminal: Terminal, tx_id: int, mode: str | None) -> AsyncGenerator[None]:
    """Hold diagnostic session ``mode`` on ``tx_id`` (with keepalives) for the block.

    Nothing is sent when ``mode`` is None: the default session is where the ECU already is.
    """
    if mode is None:
        yield
        return
    async with SessionManager(terminal).held(tx_id, mode=mode):
        yield
