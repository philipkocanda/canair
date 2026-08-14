"""``Elm327TcpTerminal`` — the ELM327 engine bound to a plain TCP socket.

The transport ``elm327-tcp``: a generic WiFi ELM327 adapter (Kiwi, vLinker,
OBDLink) or the ELM327-Emulator's ``-n`` network mode. The counterpart to
:class:`canlib.terminal.WiCANTerminal`, which binds the same engine to the WiCAN's
WebSocket — a wire binding is deliberately a separate module from the engine, so
"support a new ELM327 wire" stays a new :class:`~canlib.transport.channel.Channel`
plus a few lines here, never a second copy of the protocol logic.

Unlike the WiCAN there is no HTTP config API and no ``reboot``, which is why
``TransportSpec.wican_http`` is false for this transport.
"""

from __future__ import annotations

from .channel import TcpChannel
from .elm327_frame_count import CountKey
from .elm327_terminal import Elm327Terminal


class Elm327TcpTerminal(Elm327Terminal):
    """ELM327 engine over a direct TCP socket (transport ``elm327-tcp``)."""

    def __init__(
        self,
        host: str,
        port: int,
        timeout: float = 3.0,
        verbose: bool = False,
        unsafe: bool = False,
        hk_f1xx_offset: bool = False,
        expected_responses: bool = True,
        response_frames: dict[CountKey, int] | None = None,
    ):
        self.port = port
        channel = TcpChannel(host, port, verbose=verbose)
        super().__init__(
            channel,
            timeout=timeout,
            verbose=verbose,
            unsafe=unsafe,
            hk_f1xx_offset=hk_f1xx_offset,
            expected_responses=expected_responses,
            response_frames=response_frames,
        )
