"""The ELM327 side of a functional request: every reply, with the CAN id it came on.

canair reads ELM327 replies with headers off, which hides who answered. A
functional sweep needs exactly that, so for one request the adapter is switched
to headers on (``ATH1``), addressed at the functional id with its own receive
filter and flow control (:func:`~canlib.transport.elm327_addressing.functional_target`,
via ``set_header``), and switched back (``ATH0``), all in one transaction so a
keepalive can never read a headers-on reply. The adapter keeps collecting replies
until its ``ATST`` wait runs out after the last one; the wait is raised for the
request when the window is longer.

Best-effort compared with ``slcan-tcp``: the adapter, not canair, sends flow
control and decides when to stop, and overlapping multi-frame replies from
several ECUs are the case it handles worst. Single-frame requests (``3E 00``) are
the reliable ones.
"""

from __future__ import annotations

import math
from dataclasses import replace
from typing import TYPE_CHECKING

from .errors import AdapterRefusedError
from .functional import DEFAULT_WINDOW_S, FunctionalCollector, FunctionalResult

if TYPE_CHECKING:
    from .elm327_terminal import Elm327Terminal

# ATST counts in 4.096 ms units, one byte wide.
_ST_UNIT_S = 0.004096
_ST_MAX = 0xFF


def parse_header_lines(text: str, extended: bool) -> list[tuple[int, bytes]]:
    """``(can_id, frame data)`` for every headers-on reply line in ``text``.

    Each line is the CAN id (3 hex digits, 8 for a 29-bit id) followed by the
    frame's data, PCI byte first, with or without spaces (``ATS0``). Anything
    else (an echoed request, ``NO DATA``, ``OK``, the prompt) is skipped: an echo
    never parses, because a request has an even number of digits and an id an
    odd one, or (29-bit) too few for an id and a PCI byte.
    """
    width = 8 if extended else 3
    frames: list[tuple[int, bytes]] = []
    for line in text.replace("\r", "\n").split("\n"):
        clean = line.replace(" ", "").strip().rstrip(">").upper()
        body = clean[width:]
        if len(clean) <= width or len(body) % 2:
            continue
        try:
            frames.append((int(clean[:width], 16), bytes.fromhex(body)))
        except ValueError:
            continue
    return frames


def _st_command(seconds: float) -> str:
    return f"ATST{min(_ST_MAX, max(1, math.ceil(seconds / _ST_UNIT_S))):02X}"


async def elm_functional_exchange(
    terminal: Elm327Terminal,
    tx_id: int,
    request: bytes,
    window: float = DEFAULT_WINDOW_S,
) -> FunctionalResult:
    """Broadcast ``request`` on ``tx_id`` through the adapter and parse every reply."""
    extended = tx_id > 0x7FF
    budget = terminal.elm_response_budget()
    raise_wait = window > budget
    async with terminal.transaction():
        await terminal.set_header(tx_id)
        try:
            on = terminal.headers_command(True)
            if on is not None and "?" in await terminal.send_command(on):
                raise AdapterRefusedError(
                    f"the adapter rejected {on}, so it cannot show which ECU sent each reply"
                )
            if raise_wait:
                await terminal.send_command(_st_command(window))
            text = await terminal.send_command(
                request.hex().upper(), timeout=2 * max(window, budget) + 2.0
            )
        finally:
            off = terminal.headers_command(False)
            if off is not None:
                await terminal.send_command(off)
            if raise_wait:
                await terminal.send_command(terminal.elm_timeout_cmd)
    # The adapter hands every reply back at once, so arrival times are lost; the
    # collector only reassembles and judges here.
    collector = FunctionalCollector(tx_id, request, started=0.0, window=0.0)
    for can_id, data in parse_header_lines(text, extended):
        collector.feed(can_id, data, 0.0)
    result = collector.result()
    result.messages = [replace(m, elapsed_ms=None) for m in result.messages]
    return result
