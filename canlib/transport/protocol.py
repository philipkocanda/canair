"""The ``Terminal`` protocol — the async surface every transport exposes.

canair reaches the CAN bus through one of two transports
(:class:`canlib.terminal.WiCANTerminal` over the WebSocket ELM327 terminal,
:class:`canlib.transport.raw_terminal.RawTerminal` over raw SLCAN + client-side
ISO-TP), and every live command is dispatched through the shared
:func:`canlib.commands._live.dispatch_mode` against *this surface only*. Typing
the seam against :class:`Terminal` — rather than a concrete class — is the
compiler-checked form of the "keep the WiCAN replaceable" rule: a new backend
slots in by structurally satisfying the protocol, and ``ty`` rejects any mode
that reaches for a concrete-terminal-only attribute.

Structural typing needs no nominal base, so the real terminals do not inherit
this. It is :func:`runtime_checkable` so a smoke test can assert conformance
(``isinstance(term, Terminal)``); note that only guards method *presence*, not
signatures — the real oracle is ``ty check`` over the retyped seam.
"""

from __future__ import annotations

import asyncio
from typing import Protocol, runtime_checkable

from ..uds_parse import UdsResponse


@runtime_checkable
class Terminal(Protocol):
    """The async surface both real terminals (and the test fake) implement.

    Modes are written against THIS, never a concrete terminal, so a new backend
    slots in by implementing it (the "keep the WiCAN replaceable" rule).
    """

    async def set_header(self, tx_id: int) -> None: ...

    async def send_uds(
        self,
        service_pid: str,
        timeout: float | None = ...,
        expected_sid: int | None = ...,
        expected_did: int | None = ...,
        expected_echo: bytes | None = ...,
        retries: int = ...,
    ) -> UdsResponse: ...

    async def send_command(self, cmd: str, timeout: float | None = ...) -> str: ...

    async def enter_extended_session(
        self, wake: bool = ..., mode: str = ...
    ) -> tuple[bool, asyncio.Task | None]: ...

    async def close(self) -> None: ...
