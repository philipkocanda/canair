"""Tests for the direct-ELM327 TCP transport (canlib.transport, elm327-tcp).

Device-free: a fake asyncio stream drives the real :class:`TcpChannel` framing,
and a fake :class:`Channel` drives the real :class:`Elm327Terminal` engine — so
the ELM327 protocol logic is proven transport-agnostic (works over any channel,
not just the WiCAN WebSocket) without a live socket.

An opt-in end-to-end test against the ELM327-Emulator lives in
``test_elm327_emulator.py`` (skipped when the emulator isn't installed).
"""

import asyncio
import time
from contextlib import asynccontextmanager
from pathlib import Path

import pytest
import yaml

from canlib.decoding import decode_param_rows
from canlib.transport import channel
from canlib.transport.channel import Channel, TcpChannel
from canlib.transport.elm327_tcp import Elm327TcpTerminal
from canlib.transport.elm327_terminal import Elm327Terminal

_EMU_ENGINE_YAML = Path(__file__).parent / "fixtures/profiles/elm327-emulator/ecus/engine.yaml"


class FakeStreamReader:
    """Minimal asyncio.StreamReader stand-in yielding queued byte chunks."""

    def __init__(self):
        self._q: asyncio.Queue[bytes] = asyncio.Queue()

    def feed(self, data: bytes) -> None:
        self._q.put_nowait(data)

    async def read(self, _n: int) -> bytes:
        return await self._q.get()


class FakeStreamWriter:
    """Minimal asyncio.StreamWriter stand-in recording written bytes."""

    def __init__(self):
        self.written: list[bytes] = []
        self.closed = False

    def write(self, data: bytes) -> None:
        self.written.append(data)

    async def drain(self) -> None:
        pass

    def close(self) -> None:
        self.closed = True

    async def wait_closed(self) -> None:
        pass


def _tcp_channel() -> TcpChannel:
    ch = TcpChannel("host", 35000)
    ch._reader = FakeStreamReader()
    ch._writer = FakeStreamWriter()
    return ch


class TestTcpChannel:
    @pytest.mark.asyncio
    async def test_send_writes_ascii_bytes(self):
        ch = _tcp_channel()
        await ch.send("ATZ\r")
        assert ch._writer.written == [b"ATZ\r"]

    @pytest.mark.asyncio
    async def test_recv_decodes_chunk(self):
        ch = _tcp_channel()
        ch._reader.feed(b"OK\r>")
        assert await ch.recv(1.0) == "OK\r>"

    @pytest.mark.asyncio
    async def test_recv_timeout_returns_none(self):
        ch = _tcp_channel()  # nothing fed -> read() blocks -> wait_for times out
        assert await ch.recv(0.05) is None

    @pytest.mark.asyncio
    async def test_recv_eof_raises_connection_error(self):
        ch = _tcp_channel()
        ch._reader.feed(b"")  # EOF: peer closed
        with pytest.raises(ConnectionError):
            await ch.recv(1.0)

    @pytest.mark.asyncio
    async def test_drain_eof_raises_connection_error(self):
        # A drain runs as the first half of a pipe resync; treating EOF as "the
        # line went quiet" made a dead socket look successfully realigned.
        ch = _tcp_channel()
        ch._reader.feed(b"")
        with pytest.raises(ConnectionError):
            await ch.drain(per_recv_timeout=0.05, max_seconds=0.5)

    @pytest.mark.asyncio
    async def test_close_closes_writer(self):
        ch = _tcp_channel()
        writer = ch._writer
        await ch.close()
        assert writer.closed
        assert ch._reader is None and ch._writer is None

    def test_transport_name_labels_diag(self):
        # The engine tags its per-exchange diagnostics with the channel's name.
        term = Elm327TcpTerminal("host", 35000)
        assert term.diag.transport == "elm327-tcp"


class TestTcpChannelConnect:
    """connect() must apply the same three guarantees as WebSocketChannel.

    Regression: TcpChannel.connect() was a bare `asyncio.open_connection` — no
    timeout, no settle, no drain — while its WebSocket twin had all three.
    """

    @staticmethod
    @asynccontextmanager
    async def _serving(handler):
        """An ephemeral loopback TCP server, torn down without `wait_closed()`.

        `asyncio.Server.wait_closed()` blocks until in-flight handler tasks finish
        (and hangs outright on some 3.12 patch levels), so it's deliberately not
        awaited here — `close()` releases the listening socket, which is all these
        tests need.
        """
        server = await asyncio.start_server(handler, "127.0.0.1", 0)
        try:
            yield server.sockets[0].getsockname()[1]
        finally:
            server.close()

    @pytest.mark.asyncio
    async def test_drains_the_connect_banner(self):
        """A clone's greeting must not be consumed as the first command's reply.

        The engine frames a reply by its `>` prompt, so an undrained
        `ELM327 v1.5\\r\\r>` banner is returned as the reply to whatever command is
        sent first, leaving that command's real reply buffered — shifting every
        response by one for the rest of the session.
        """

        async def handler(reader, writer):
            writer.write(b"\r\rELM327 v1.5\r\r>")  # connect banner
            await writer.drain()
            await reader.read(64)  # the first command
            writer.write(b"ATZ\rELM327 v1.5\r\r>")  # its actual reply
            await writer.drain()

        async with self._serving(handler) as port:
            ch = TcpChannel("127.0.0.1", port)
            await ch.connect()
            await ch.send("ATZ\r")
            reply = await ch.recv(2.0)
            assert reply is not None
            assert "ATZ" in reply, f"banner leaked into the first reply: {reply!r}"
            await ch.close()

    @pytest.mark.asyncio
    async def test_connect_timeout_raises_connection_error(self, monkeypatch):
        """A host that accepts then stalls must fail fast, not hang.

        Without a timeout this blocked for the OS TCP timeout (~75s on
        Darwin/Linux), which also overran the monitor's reconnect budget.
        """
        monkeypatch.setattr(channel, "CONNECT_TIMEOUT", 0.2)

        async def never_connects(*args, **kwargs):
            await asyncio.sleep(30)

        monkeypatch.setattr(asyncio, "open_connection", never_connects)
        ch = TcpChannel("10.255.255.1", 35000)
        started = time.monotonic()
        with pytest.raises(ConnectionError, match="did not respond"):
            await ch.connect()
        assert time.monotonic() - started < 5.0

    @pytest.mark.asyncio
    async def test_connect_succeeds_against_a_silent_adapter(self):
        # No banner at all is the other valid case — the drain must simply find
        # nothing and connect must still succeed.
        async def handler(reader, writer):
            await reader.read(64)

        async with self._serving(handler) as port:
            ch = TcpChannel("127.0.0.1", port)
            await ch.connect()
            assert ch._reader is not None and ch._writer is not None
            await ch.close()


class FakeChannel:
    """A programmable :class:`Channel`: each send() queues the next canned reply.

    Replies are ELM327-style text ending in the `>` prompt; recv() returns them
    chunk-by-chunk and then times out (None) like a real quiet line.
    """

    transport_name = "elm327-tcp"

    def __init__(self, replies):
        self.sent: list[str] = []
        self._replies = list(replies)
        self._q: asyncio.Queue[str] = asyncio.Queue()
        self.connected = False
        self.closed = False

    async def connect(self) -> None:
        self.connected = True

    async def send(self, text: str) -> None:
        self.sent.append(text)
        reply = self._replies.pop(0) if self._replies else "NO DATA\r>"
        await self._q.put(reply)

    async def recv(self, timeout: float) -> str | None:
        try:
            return self._q.get_nowait()
        except asyncio.QueueEmpty:
            await asyncio.sleep(min(timeout, 0.001))
            return None

    async def drain(self, per_recv_timeout: float = 0.2, max_seconds: float = 1.0) -> None:
        while not self._q.empty():
            self._q.get_nowait()

    async def close(self) -> None:
        self.closed = True


class TestEngineOverArbitraryChannel:
    """The ELM327 engine drives ANY channel — not just the WebSocket."""

    def test_fake_channel_satisfies_protocol(self):
        assert isinstance(FakeChannel([]), Channel)

    @pytest.mark.asyncio
    async def test_send_uds_over_fake_channel(self):
        term = Elm327Terminal(FakeChannel(["6101AA\r>"]))
        resp = await term.send_uds("2101")
        assert resp["ok"] is True
        assert resp["hex"] == "6101AA"

    @pytest.mark.asyncio
    async def test_set_header_and_read(self):
        ch = FakeChannel(["OK\r>", "OK\r>", "6101AA\r>"])
        term = Elm327Terminal(ch)
        await term.set_header(0x7E4)
        await term.send_uds("2101")
        # Header pair sent once, then the UDS request.
        assert [s.rstrip("\r") for s in ch.sent] == ["ATSH7E4", "ATFCSH7E4", "2101"]

    @pytest.mark.asyncio
    async def test_connect_and_close_delegate_to_channel(self):
        ch = FakeChannel([])
        term = Elm327Terminal(ch)
        await term.connect()
        assert ch.connected
        await term.close()
        assert ch.closed

    @pytest.mark.asyncio
    async def test_nrc_is_returned(self):
        term = Elm327Terminal(FakeChannel(["7F2112\r>"]))
        resp = await term.send_uds("2101")
        assert resp["ok"] is False
        assert resp["nrc"] == 0x12

    @pytest.mark.asyncio
    async def test_recv_frame_and_drain_go_through_channel(self):
        # Passive frame collection (used by skm_wakeup) goes through the channel
        # surface, not the raw socket — proving the WebSocket leak is closed.
        class RecordingChannel:
            transport_name = "x"

            def __init__(self, frames):
                self.frames = list(frames)
                self.drained = False

            async def connect(self):
                pass

            async def send(self, text):
                pass

            async def recv(self, timeout):
                return self.frames.pop(0) if self.frames else None

            async def drain(self, per_recv_timeout=0.2, max_seconds=1.0):
                self.drained = True

            async def close(self):
                pass

        ch = RecordingChannel(["6FB100", None])
        term = Elm327Terminal(ch)
        assert await term.recv_frame(0.1) == "6FB100"
        assert await term.recv_frame(0.1) is None
        await term.drain()
        assert ch.drained

    def test_host_derived_from_channel(self):
        # Single source of truth: the engine reads host off its channel.
        assert Elm327TcpTerminal("1.2.3.4", 35000).host == "1.2.3.4"
        from canlib.terminal import WiCANTerminal

        assert WiCANTerminal(host="10.0.2.86").host == "10.0.2.86"


class TestElmEngineGate:
    """skm-wake / the interactive REPL gate on the ELM327 *engine* type, so they
    run on any ELM transport (wican-ws / elm327-tcp) but are refused on raw
    slcan-tcp (RawTerminal, which parses no ELM ATSH text)."""

    def test_elm_tcp_is_elm_engine(self):
        assert issubclass(Elm327TcpTerminal, Elm327Terminal)
        from canlib.terminal import WiCANTerminal

        assert issubclass(WiCANTerminal, Elm327Terminal)

    def test_raw_terminal_is_not_elm_engine(self):
        from canlib.transport.raw_terminal import RawTerminal

        assert not issubclass(RawTerminal, Elm327Terminal)


class TestEmulatorProfile:
    """Guard the bundled test profile that targets ELM327-Emulator.

    The `elm327-emulator` fixture profile (tests/fixtures/profiles/) defines an
    ENGINE ECU with standard OBD-II Mode-01 PIDs so `canair read ENGINE:010F`
    works against a running emulator (see docs/development/offline-testing.md).
    These device-free tests lock the decode contract (the `Bnn` byte offsets)
    and stop the fixture bit-rotting — using the real decode path, no socket.
    """

    def _params(self, pid: str) -> dict:
        engine = yaml.safe_load(_EMU_ENGINE_YAML.read_text())["ENGINE"]
        return engine["pids"][pid]["parameters"]

    def _decode(self, pid: str, payload_hex: str, name: str) -> float:
        rows = decode_param_rows(payload_hex, self._params(pid))
        row = next(r for r in rows if r[0] == name)
        assert row[4] is None, f"decode error: {row[4]}"  # index 4 = error
        return row[1]  # index 1 = value

    def test_intake_temp(self):
        # 41 0F 44 -> B03=0x44 -> 0x44 - 40 = 28 degC
        assert self._decode("010F", "410F44", "INTAKE_TEMP_C") == 28

    def test_vehicle_speed(self):
        # 41 0D 10 -> B03=0x10 = 16 km/h
        assert self._decode("010D", "410D10", "VEHICLE_SPEED") == 16

    def test_engine_rpm(self):
        # 41 0C 0B 90 -> (B03*256 + B04)/4 = (0x0B*256 + 0x90)/4 = 740 rpm
        assert self._decode("010C", "410C0B90", "ENGINE_RPM") == 740

    def test_coolant_temp_leading_zero_pid(self):
        # PID key "0105" is all-decimal-with-leading-zero — quoted so it round-
        # trips (see canlib.pids_edit._key_token). 41 05 7B -> 0x7B - 40 = 83 degC.
        assert self._decode("0105", "41057B", "COOLANT_TEMP_C") == 83
