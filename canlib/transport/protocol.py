"""The ``Terminal`` protocol — the async surface every transport exposes.

canair reaches the CAN bus through one of two transports
(:class:`canlib.terminal.WiCANTerminal` over the WebSocket ELM327 terminal,
:class:`canlib.transport.raw_terminal.RawTerminal` over raw SLCAN + client-side
ISO-TP), and every live command is dispatched through the shared
:func:`canlib.modes.dispatch.dispatch_mode` against *this surface only*. Typing
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
from contextlib import AbstractAsyncContextManager
from typing import Protocol, runtime_checkable

from ..frame_counts import FrameCountLedger
from ..timing import TimingRecorder
from ..transport_stats import TransportStats
from ..uds_parse import UdsResponse


@runtime_checkable
class Terminal(Protocol):
    """The async surface both real terminals (and the test fake) implement.

    Modes are written against THIS, never a concrete terminal, so a new backend
    slots in by implementing it (the "keep the WiCAN replaceable" rule).
    """

    # Per-exchange outcome tally (drops/errors) — read by the monitor for its
    # live status line and recorded-capture provenance.
    diag: TransportStats

    # Per-(ECU, PID) round-trip timings — read by `--timings` after a session.
    timings: TimingRecorder

    # What this session observed about response frame counts, per request. Both
    # families feed it (an ELM327 from the adapter's frame lines, the raw path from
    # its own reassembly), so the write-back that persists a confirmed count into
    # the profile's `response_frames:` needs no knowledge of the transport.
    frame_counts: FrameCountLedger

    # Largest UDS request, in data bytes, this transport can put on the wire as
    # one exchange. A transport that runs ISO-TP itself segments freely; an
    # ELM327 does not, and rejects an over-long request outright. Callers that
    # *build* a request whose size they control (multi-DID batching) must clamp
    # to this rather than discover the ceiling from a failure.
    max_request_bytes: int

    async def set_header(self, tx_id: int) -> None: ...

    def transaction(self) -> AbstractAsyncContextManager[None]:
        """Group several commands into one non-interleavable exchange.

        Needed because a UDS read is *stateful* on an ELM327 — the header is set
        by a separate command — so a concurrent keepalive landing between the
        header and the request retargets it. Backends with no such shared state
        (the raw ISO-TP path addresses every frame explicitly) satisfy this with a
        no-op.
        """
        ...

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
