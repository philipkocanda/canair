"""Tests for the direct-ELM327 TCP transport (canlib.transport, elm327-tcp).

Device-free: a fake asyncio stream drives the real :class:`TcpChannel` framing,
and a fake :class:`Channel` drives the real :class:`Elm327Terminal` engine — so
the ELM327 protocol logic is proven transport-agnostic (works over any channel,
not just the WiCAN WebSocket) without a live socket.

An opt-in end-to-end test against the ELM327-Emulator lives in
``test_elm327_emulator.py`` (skipped when the emulator isn't installed).
"""

import asyncio
from pathlib import Path

import pytest
import yaml

from canlib.decoding import decode_param_rows
from canlib.transport.channel import Channel, TcpChannel
from canlib.transport.elm327_terminal import Elm327TcpTerminal, Elm327Terminal

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
