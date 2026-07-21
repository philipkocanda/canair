"""Tests for canlib.transport.slcan_tcp — SLCAN framing/parse + TCP bus.

A fake socket exercises the real SlcanTcpBus open/send/recv logic without
hardware.
"""

import can
import pytest

from canlib.transport import slcan_tcp
from canlib.transport.slcan_tcp import SlcanTcpBus, format_slcan_frame, parse_slcan_frame


class TestFormat:
    def test_standard_data_frame(self):
        m = can.Message(arbitration_id=0x7E4, is_extended_id=False, data=bytes.fromhex("0201"))
        assert format_slcan_frame(m) == "t7E420201\r"

    def test_full_8_byte_frame(self):
        m = can.Message(arbitration_id=0x770, is_extended_id=False, data=bytes(range(8)))
        assert format_slcan_frame(m) == "t7708" + "0001020304050607" + "\r"

    def test_extended_frame(self):
        m = can.Message(arbitration_id=0x18DAF110, is_extended_id=True, data=b"\xaa")
        assert format_slcan_frame(m) == "T18DAF110" + "1" + "AA" + "\r"

    def test_remote_frame_has_no_data(self):
        m = can.Message(arbitration_id=0x123, is_extended_id=False, is_remote_frame=True, dlc=8)
        assert format_slcan_frame(m) == "r1238\r"


class TestParse:
    def test_standard(self):
        m = parse_slcan_frame("t7E430201FF")  # id 7E4, dlc 3
        assert m.arbitration_id == 0x7E4
        assert not m.is_extended_id
        assert m.dlc == 3
        assert m.data == bytes.fromhex("0201FF")

    def test_extended(self):
        m = parse_slcan_frame("T18DAF1101AA")
        assert m.is_extended_id
        assert m.arbitration_id == 0x18DAF110
        assert m.data == b"\xaa"

    def test_remote(self):
        m = parse_slcan_frame("r1238")
        assert m.is_remote_frame
        assert m.arbitration_id == 0x123
        assert m.dlc == 8
        assert m.data == b""

    def test_trailing_timestamp_ignored(self):
        # Lawicel Z1 appends a 4-hex ms stamp after the data; DLC bounds the data.
        m = parse_slcan_frame("t7E48" + "0011223344556677" + "1A2B")
        assert m.dlc == 8
        assert m.data == bytes.fromhex("0011223344556677")

    def test_roundtrip(self):
        m = can.Message(arbitration_id=0x123, is_extended_id=False, data=bytes.fromhex("DEADBEEF"))
        line = format_slcan_frame(m).rstrip("\r")
        back = parse_slcan_frame(line)
        assert back.arbitration_id == 0x123
        assert back.data == m.data

    @pytest.mark.parametrize("line", ["", "\r", "V1011", "N1234", "z", "x99", "\a"])
    def test_non_frame_lines_return_none(self, line):
        assert parse_slcan_frame(line) is None

    def test_truncated_data_returns_none(self):
        # DLC says 8 bytes but only 2 provided.
        assert parse_slcan_frame("t7E480011") is None


class FakeSocket:
    """Minimal non-blocking socket stand-in for SlcanTcpBus."""

    def __init__(self, rx_chunks=()):
        self.sent = bytearray()
        self._rx = list(rx_chunks)
        self.closed = False

    def setblocking(self, flag):
        pass

    def sendall(self, data):
        self.sent += data

    def recv(self, n):
        return self._rx.pop(0) if self._rx else b""

    def close(self):
        self.closed = True

    def feed(self, data: bytes):
        self._rx.append(data)


@pytest.fixture
def patched_bus(monkeypatch):
    """SlcanTcpBus wired to a FakeSocket; select reports readable iff data queued."""
    fake = FakeSocket()
    monkeypatch.setattr(slcan_tcp.socket, "create_connection", lambda *a, **k: fake)
    monkeypatch.setattr(
        slcan_tcp.select,
        "select",
        lambda r, w, x, t: (list(r), [], []) if fake._rx else ([], [], []),
    )
    bus = SlcanTcpBus("10.0.0.9", port=3333, bitrate=500_000)
    return bus, fake


class TestBus:
    def test_open_sequence(self, patched_bus):
        bus, fake = patched_bus
        # Close, bitrate S6 (500k), open O — each CR-terminated.
        assert fake.sent.decode() == "C\rS6\rO\r"
        bus.shutdown()

    def test_listen_only_uses_L(self, monkeypatch):
        fake = FakeSocket()
        monkeypatch.setattr(slcan_tcp.socket, "create_connection", lambda *a, **k: fake)
        monkeypatch.setattr(slcan_tcp.select, "select", lambda r, w, x, t: ([], [], []))
        bus = SlcanTcpBus("h", listen_only=True)
        assert fake.sent.decode() == "C\rS6\rL\r"
        bus.shutdown()

    def test_bad_bitrate_rejected(self, monkeypatch):
        monkeypatch.setattr(slcan_tcp.socket, "create_connection", lambda *a, **k: FakeSocket())
        with pytest.raises(ValueError):
            SlcanTcpBus("h", bitrate=42)

    def test_send_formats_frame(self, patched_bus):
        bus, fake = patched_bus
        fake.sent.clear()
        bus.send(
            can.Message(arbitration_id=0x7E4, is_extended_id=False, data=bytes.fromhex("022101"))
        )
        assert fake.sent.decode() == "t7E43022101\r"
        bus.shutdown()

    def test_recv_parses_frames(self, patched_bus):
        bus, fake = patched_bus
        fake.feed(b"t7E430201FF\rt77080011223344556677\r")
        m1 = bus.recv(timeout=0.1)
        m2 = bus.recv(timeout=0.1)
        assert m1.arbitration_id == 0x7E4 and m1.data == bytes.fromhex("0201FF")
        assert m2.arbitration_id == 0x770 and m2.dlc == 8
        bus.shutdown()

    def test_recv_handles_partial_lines(self, patched_bus):
        bus, fake = patched_bus
        fake.feed(b"t7E43")  # partial (no CR yet)
        assert bus.recv(timeout=0.05) is None
        fake.feed(b"0201FF\r")  # completes the record
        m = bus.recv(timeout=0.05)
        assert m is not None and m.data == bytes.fromhex("0201FF")
        bus.shutdown()

    def test_shutdown_sends_close_and_closes_socket(self, patched_bus):
        bus, fake = patched_bus
        fake.sent.clear()
        bus.shutdown()
        assert fake.sent.decode() == "C\r"
        assert fake.closed
