"""Byte-stream channels for the ELM327 protocol engine.

An :class:`~canlib.transport.elm327_terminal.Elm327Terminal` speaks the ELM327
ASCII protocol against a :class:`Channel` — the thin async seam that actually
moves bytes and owns any transport-specific framing/decoding. The same engine
therefore drives a WiCAN's WebSocket ELM327 terminal, a direct ELM327 clone over
a plain TCP socket, or (future) a serial dongle, by swapping only the channel.

A channel's :meth:`Channel.recv` returns *already-decoded* terminal text (any
WebSocket-JSON envelope unwrapped) so the engine's prompt-accumulation loop is
identical across transports. It returns ``None`` on a receive timeout (the engine
treats that as "no data yet"), and raises :class:`ConnectionError` when the link
drops (the engine lets that propagate to the transport-error guard).
"""

from __future__ import annotations

import asyncio
import json
import sys
import time
from typing import Protocol, runtime_checkable

try:
    import websockets
    from websockets.asyncio.client import ClientConnection
except ImportError as e:  # pragma: no cover - import-time guard
    raise ImportError("websockets not installed. Run: pip3 install websockets") from e


@runtime_checkable
class Channel(Protocol):
    """The async byte-stream surface an ELM327 engine talks to.

    Each implementation owns its own connect handshake and framing; the engine
    only ever sends ELM327 command text and consumes decoded reply text.
    """

    async def connect(self) -> None: ...

    async def send(self, text: str) -> None: ...

    async def recv(self, timeout: float) -> str | None:
        """Return the next decoded terminal text, ``None`` on timeout.

        Raises :class:`ConnectionError` if the underlying link has dropped.
        """
        ...

    async def drain(self, per_recv_timeout: float = 0.2, max_seconds: float = 1.0) -> None: ...

    async def close(self) -> None: ...


class WebSocketChannel:
    """ELM327 channel over a WiCAN's ``ws://host/ws`` terminal.

    Owns the WebSocket connect + ``{"ws_mode": "terminal"}`` handshake and the
    ``{"type": "term_out", "data": …}`` JSON unwrap, so the engine sees only the
    decoded ELM327 text (exactly as a raw serial/TCP channel would).
    """

    # Labels the transport in the engine's per-exchange diagnostics tally.
    transport_name = "wican-ws"

    def __init__(self, host: str, verbose: bool = False):
        self.host = host
        self.url = f"ws://{host}/ws"
        self.verbose = verbose
        self.ws: ClientConnection | None = None

    async def connect(self) -> None:
        if self.verbose:
            print(f"  [ws] Connecting to {self.url}...", file=sys.stderr)
        self.ws = await websockets.connect(self.url, ping_interval=None, open_timeout=10.0)
        mode_msg = json.dumps({"ws_mode": "terminal", "terminal_type": "elm327"})
        await self.ws.send(mode_msg)
        if self.verbose:
            print(f"  [ws] Sent: {mode_msg}", file=sys.stderr)
        await asyncio.sleep(0.3)
        await self.drain()

    async def send(self, text: str) -> None:
        assert self.ws is not None  # connected before use
        await self.ws.send(text)
        if self.verbose:
            print(f"  [ws] Sent: {text!r}", file=sys.stderr)

    async def recv(self, timeout: float) -> str | None:
        assert self.ws is not None  # connected before use
        try:
            msg = await asyncio.wait_for(self.ws.recv(), timeout=timeout)
        except TimeoutError:
            return None
        except websockets.exceptions.ConnectionClosed as e:
            raise ConnectionError("WebSocket connection closed") from e

        if not isinstance(msg, str):
            # A binary frame: not terminal text — treat as a no-op control message.
            return ""
        try:
            parsed = json.loads(msg)
        except json.JSONDecodeError:
            if self.verbose:
                print(f"  [ws] Recv (text): {msg!r}", file=sys.stderr)
            return msg
        if isinstance(parsed, dict):
            if parsed.get("type") == "term_out":
                data = parsed["data"]
                if self.verbose:
                    print(f"  [ws] Recv (term_out): {data!r}", file=sys.stderr)
                return data
            # ws_mode ack or any other control message: no terminal text.
            if self.verbose:
                print(f"  [ws] Recv (json): {parsed}", file=sys.stderr)
            return ""
        # Non-dict JSON (e.g. a bare number that happens to parse): treat as text.
        return msg

    async def drain(self, per_recv_timeout: float = 0.2, max_seconds: float = 1.0) -> None:
        """Read and discard any pending messages (clears stale/late frames).

        Loops until a ``per_recv_timeout`` window passes with no message, or the
        overall ``max_seconds`` budget is spent (so a continuous stream can't
        hang the drain).
        """
        if self.ws is None:
            return
        deadline = time.monotonic() + max_seconds
        while time.monotonic() < deadline:
            try:
                msg = await asyncio.wait_for(self.ws.recv(), timeout=per_recv_timeout)
                if self.verbose:
                    print(f"  [ws] Drained: {msg!r}", file=sys.stderr)
            except (TimeoutError, Exception):
                break

    async def close(self) -> None:
        if self.ws:
            try:
                await self.ws.close()
            except Exception:
                pass
            self.ws = None


class TcpChannel:
    """ELM327 channel over a plain TCP socket (a direct WiFi ELM327 adapter).

    Generic $10 WiFi clones (Kiwi, vLinker, OBDLink) — and the ELM327-Emulator's
    ``-n`` network mode — expose the ELM327 terminal as a raw ASCII byte stream on
    a TCP port (conventionally 35000), with **no** WebSocket/JSON envelope. So the
    channel just streams command text and returns decoded ASCII; the engine's
    ``>``-prompt accumulation handles reply framing exactly as for the WebSocket.
    """

    transport_name = "elm327-tcp"

    def __init__(self, host: str, port: int, verbose: bool = False):
        self.host = host
        self.port = port
        self.verbose = verbose
        self._reader: asyncio.StreamReader | None = None
        self._writer: asyncio.StreamWriter | None = None

    async def connect(self) -> None:
        if self.verbose:
            print(f"  [tcp] Connecting to {self.host}:{self.port}...", file=sys.stderr)
        self._reader, self._writer = await asyncio.open_connection(self.host, self.port)

    async def send(self, text: str) -> None:
        assert self._writer is not None  # connected before use
        self._writer.write(text.encode("ascii", errors="ignore"))
        await self._writer.drain()
        if self.verbose:
            print(f"  [tcp] Sent: {text!r}", file=sys.stderr)

    async def recv(self, timeout: float) -> str | None:
        assert self._reader is not None  # connected before use
        try:
            chunk = await asyncio.wait_for(self._reader.read(4096), timeout=timeout)
        except TimeoutError:
            return None
        if chunk == b"":
            # EOF: the peer closed the socket.
            raise ConnectionError("ELM327 TCP connection closed")
        text = chunk.decode("ascii", errors="ignore")
        if self.verbose:
            print(f"  [tcp] Recv: {text!r}", file=sys.stderr)
        return text

    async def drain(self, per_recv_timeout: float = 0.2, max_seconds: float = 1.0) -> None:
        """Read and discard any pending bytes (clears stale/late frames)."""
        if self._reader is None:
            return
        deadline = time.monotonic() + max_seconds
        while time.monotonic() < deadline:
            try:
                chunk = await asyncio.wait_for(self._reader.read(4096), timeout=per_recv_timeout)
                if chunk == b"":
                    break
                if self.verbose:
                    print(f"  [tcp] Drained: {chunk!r}", file=sys.stderr)
            except (TimeoutError, Exception):
                break

    async def close(self) -> None:
        if self._writer is not None:
            try:
                self._writer.close()
                await self._writer.wait_closed()
            except Exception:
                pass
        self._reader = None
        self._writer = None
