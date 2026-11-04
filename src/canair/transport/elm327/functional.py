"""The ELM327 side of a functional request: every reply, with the CAN id it came on.

canair reads ELM327 replies with headers off, which hides who answered. A
functional sweep needs exactly that, so for one request the adapter is switched
to headers on (``ATH1``), addressed at the functional id with flow-control mode 0
(:func:`~canair.transport.elm327.addressing.functional_target`), and its receive
filter opened to every diagnostic response id. Then everything is switched back,
all in one transaction so a keepalive can never read a headers-on reply. The
adapter keeps collecting replies until its ``ATST`` wait runs out after the last
one; the wait is raised for the request when the window is longer.

The filter has to be opened by hand: the automatic filter behind header ``0x7DF``
admits only ``0x7E8``-``0x7EF``, the OBD-II reply ids, so an ECU answering on
``0x778`` would look silent. ``ATCF``/``ATCM`` set a filter and mask (a frame
passes when its id and the filter agree on every mask bit); ``ATCRA`` with no
argument restores the automatic filter afterwards, and the next ``set_header``
restates whatever filter its ECU needs. An adapter that refuses them still runs
the sweep, and the report says which replies it may be missing.

Best-effort compared with ``slcan-tcp``: the adapter, not canair, sends flow
control and decides when to stop, and overlapping multi-frame replies from
several ECUs are the case it handles worst. Single-frame requests (``3E 00``) are
the reliable ones.
"""

from __future__ import annotations

import math
from dataclasses import replace
from typing import TYPE_CHECKING

from canair.transport.elm327.addressing import functional_target
from canair.transport.errors import AdapterRefusedError
from canair.transport.functional import (
    DEFAULT_WINDOW_S,
    FunctionalCollector,
    FunctionalResult,
    record_functional_losses,
)

if TYPE_CHECKING:
    from canair.transport.elm327.terminal import Elm327Terminal

# ATST counts in 4.096 ms units, one byte wide.
_ST_UNIT_S = 0.004096
_ST_MAX = 0xFF

# Filter + mask that pass every diagnostic response: the 11-bit block 0x700-0x7FF,
# or 29-bit physical responses to tester F1 (0x18DAF1xx).
_OPEN_FILTER_11BIT = ("ATCF700", "ATCM700")
_OPEN_FILTER_29BIT = ("ATCF18DAF100", "ATCM1FFFFF00")
# Back to the adapter's automatic filter (AdapterState tracks it as AUTO); ATAR is
# the older command for the same thing, for an adapter that rejects a bare ATCRA.
_AUTO_FILTER = "ATCRA"
_AUTO_RECEIVE = "ATAR"


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


async def _restore(terminal: Elm327Terminal, commands: list[str | None]) -> None:
    """Send every restore command even if one fails; then raise the first failure."""
    first: BaseException | None = None
    for cmd in commands:
        if cmd is None:
            continue
        try:
            reply = await terminal.send_command(cmd)
            if cmd == _AUTO_FILTER and "?" in reply:
                await terminal.send_command(_AUTO_RECEIVE)
        except Exception as exc:  # keep restoring the rest; re-raised below
            first = first or exc
    if first is not None:
        raise first


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
    notes: list[str] = []
    async with terminal.transaction():
        # The functional target directly, never a profile ECU registered on the
        # functional id: that one's own filter would hide every other responder.
        await terminal.set_header(tx_id, target=functional_target(tx_id))
        try:
            for cmd in _OPEN_FILTER_29BIT if extended else _OPEN_FILTER_11BIT:
                if "?" in await terminal.send_command(cmd):
                    notes.append(
                        f"the adapter rejected {cmd}, so its automatic receive filter applied "
                        "and ECUs answering outside the OBD-II reply ids may be missing"
                    )
                    break
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
            # Headers first: a headers-on reply is the one thing nothing else parses.
            await _restore(
                terminal,
                [
                    terminal.headers_command(False),
                    terminal.elm_timeout_cmd if raise_wait else None,
                    _AUTO_FILTER,
                ],
            )
    # The adapter hands every reply back at once, so arrival times are lost; the
    # collector only reassembles and judges here.
    collector = FunctionalCollector(tx_id, request, started=0.0, window=0.0)
    for can_id, data in parse_header_lines(text, extended):
        collector.feed(can_id, data, 0.0, extended=extended)
    result = collector.result()
    result.messages = [replace(m, elapsed_ms=None) for m in result.messages]
    result.notes = notes
    record_functional_losses(terminal.diag, result)
    return result
