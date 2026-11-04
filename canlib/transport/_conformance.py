"""Type-level conformance checks for the transport backends.

`Terminal` and `Channel` are structural :class:`~typing.Protocol` types, so a
backend satisfies them by shape rather than inheritance. Nothing *nominally*
binds a backend to its protocol, which means a drifted signature is only caught
if some mode happens to exercise that method through the typed seam.

`runtime_checkable` + ``isinstance`` doesn't close the gap: it checks method
**presence only**, never signatures. That is exactly how ``TcpChannel`` shipped
without ``connect()``'s timeout/settle/drain while still passing an
``isinstance(ch, Channel)`` smoke test, and why ``Elm327TcpTerminal`` was absent
from the conformance test module entirely without anything noticing.

This module closes it. The assignments below are checked by ``ty`` (which types
all of ``canlib/``), so adding a backend that doesn't structurally satisfy its
protocol — or changing a protocol method's signature without updating every
backend — fails the type gate with the offending class named.

Nothing imports this module and nothing calls the function: it exists purely to
be type-checked. Keep it that way — it must stay free of side effects.
"""

from __future__ import annotations

from canlib.transport.elm327.channel import Channel, TcpChannel, WebSocketChannel
from canlib.transport.elm327.tcp import Elm327TcpTerminal
from canlib.transport.elm327.terminal import Elm327Terminal
from canlib.transport.elm327.wican import WiCANTerminal
from canlib.transport.raw.terminal import RawTerminal

from .protocol import Terminal


def _static_conformance(
    wican_ws: WiCANTerminal,
    raw: RawTerminal,
    elm_engine: Elm327Terminal,
    elm_tcp: Elm327TcpTerminal,
    ws_channel: WebSocketChannel,
    tcp_channel: TcpChannel,
) -> None:
    """Never called. Each binding asserts one backend satisfies its protocol."""
    # Every transport a live command can be dispatched over.
    _terminals: list[Terminal] = [wican_ws, raw, elm_engine, elm_tcp]

    # Every byte-stream channel the shared ELM327 engine can be driven by.
    _channels: list[Channel] = [ws_channel, tcp_channel]

    # Both ELM terminals must remain interchangeable with the shared engine, so
    # features gated on `isinstance(terminal, Elm327Terminal)` (the REPL,
    # skm-wake) work on wican-ws and elm327-tcp alike.
    _elm_engines: list[Elm327Terminal] = [wican_ws, elm_tcp]
