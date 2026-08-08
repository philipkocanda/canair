"""Tests for RawTerminal (WiCANTerminal-compatible raw-CAN adapter), fake ISO-TP."""

import pytest

from canlib.link_latency import LinkLatency
from canlib.transport import raw_terminal
from canlib.transport import slcan_tcp as slcan_mod


class FakeBus:
    def __init__(self, *a, **k):
        self.shutdown_called = False
        # The real SlcanTcpBus measures the link at its TCP handshake; an
        # unmeasured estimate keeps the LAN defaults, which is what these tests want.
        self.link = LinkLatency()

    def shutdown(self):
        self.shutdown_called = True


class FakeNotifier:
    def __init__(self, *a, **k):
        pass

    def add_listener(self, *a, **k):
        pass

    def stop(self):
        pass


class FakeStack:
    def __init__(self, txid, table):
        self.txid = txid
        self.table = table
        self._resp = None

    def start(self):
        pass

    def stop(self):
        pass

    def available(self):
        return False

    def send(self, data, *a, **k):
        self._resp = self.table.get((self.txid, bytes(data)))

    def recv(self, block=False, timeout=None):
        r, self._resp = self._resp, None
        return bytearray(r) if r is not None else None


@pytest.fixture
def make_terminal(monkeypatch):
    def build(table):
        monkeypatch.setattr(slcan_mod, "SlcanTcpBus", FakeBus)
        monkeypatch.setattr(raw_terminal.can, "Notifier", FakeNotifier)
        monkeypatch.setattr(
            raw_terminal.isotp,
            "NotifierBasedCanStack",
            lambda bus, notifier, address=None, params=None: FakeStack(address._txid, table),
        )
        monkeypatch.setattr(raw_terminal.time, "sleep", lambda *_a: None)  # skip settle
        return raw_terminal.RawTerminal("h", 3333, 500000, timeout=0.3)

    return build


class TestRawTerminalSendUds:
    @pytest.mark.asyncio
    async def test_positive_response(self, make_terminal):
        t = make_terminal({(0x770, bytes.fromhex("22BC03")): bytes.fromhex("62BC03FDEE")})
        await t.set_header(0x770)
        r = await t.send_uds("22BC03")
        assert r["ok"] is True
        assert r["hex"] == "62BC03FDEE"
        assert r["bytes"] == bytes.fromhex("62BC03FDEE")
        await t.close()

    @pytest.mark.asyncio
    async def test_did_echo_validation(self, make_terminal):
        t = make_terminal({(0x770, bytes.fromhex("22BC03")): bytes.fromhex("62BC03FDEE")})
        await t.set_header(0x770)
        r = await t.send_uds("22BC03", expected_sid=0x22, expected_did=0xBC03)
        assert r["ok"] is True
        await t.close()

    @pytest.mark.asyncio
    async def test_negative_response_nrc(self, make_terminal):
        t = make_terminal({(0x7A0, bytes.fromhex("22B004")): bytes.fromhex("7F2213")})
        await t.set_header(0x7A0)
        r = await t.send_uds("22B004")
        assert r["ok"] is False
        assert r["nrc"] == 0x13
        await t.close()

    @pytest.mark.asyncio
    async def test_timeout_no_data(self, make_terminal):
        t = make_terminal({})  # nothing in table -> recv returns None
        await t.set_header(0x770)
        r = await t.send_uds("22BC03")
        assert r["ok"] is False
        assert "NO DATA" in r["error"]
        await t.close()

    @pytest.mark.asyncio
    async def test_set_header_switches_ecu(self, make_terminal):
        t = make_terminal(
            {
                (0x770, bytes.fromhex("2101")): bytes.fromhex("6101AA"),
                (0x7E4, bytes.fromhex("2101")): bytes.fromhex("6101BB"),
            }
        )
        await t.set_header(0x770)
        assert (await t.send_uds("2101"))["hex"] == "6101AA"
        await t.set_header(0x7E4)
        assert (await t.send_uds("2101"))["hex"] == "6101BB"
        await t.close()

    @pytest.mark.asyncio
    async def test_waits_through_response_pending(self, monkeypatch):
        # ECU replies 7F 19 78 (ResponsePending) then the real answer on the next
        # recv. RawTerminal must keep reading and return the final response —
        # parity with the ELM327 path.
        class SeqStack:
            def __init__(self, txid, seq):
                self.txid = txid
                self._seq = list(seq)

            def start(self):
                pass

            def stop(self):
                pass

            def available(self):
                return False

            def send(self, data, *a, **k):
                pass

            def recv(self, block=False, timeout=None):
                return bytearray(self._seq.pop(0)) if self._seq else None

        seq = [bytes.fromhex("7F1978"), bytes.fromhex("5902FF0123002F")]
        monkeypatch.setattr(slcan_mod, "SlcanTcpBus", FakeBus)
        monkeypatch.setattr(raw_terminal.can, "Notifier", FakeNotifier)
        monkeypatch.setattr(
            raw_terminal.isotp,
            "NotifierBasedCanStack",
            lambda bus, notifier, address=None, params=None: SeqStack(address._txid, seq),
        )
        monkeypatch.setattr(raw_terminal.time, "sleep", lambda *_a: None)
        t = raw_terminal.RawTerminal("h", 3333, 500000, timeout=0.3)
        await t.set_header(0x7A0)
        r = await t.send_uds("1902FF", expected_sid=0x19)
        assert r["ok"] is True
        assert r["hex"] == "5902FF0123002F"
        await t.close()

    @pytest.mark.asyncio
    async def test_retries_on_no_data_then_succeeds(self, monkeypatch):
        # First exchange returns NO DATA (recv None); with retries=1 the second
        # exchange returns the real answer. An NRC would NOT be retried.
        class RetryStack:
            def __init__(self, txid, responses):
                self.txid = txid
                self._responses = list(responses)
                self._pending = None

            def start(self):
                pass

            def stop(self):
                pass

            def available(self):
                return False

            def send(self, data, *a, **k):
                self._pending = self._responses.pop(0) if self._responses else None

            def recv(self, block=False, timeout=None):
                r, self._pending = self._pending, None
                return bytearray(r) if r is not None else None

        responses = [None, bytes.fromhex("62BC03FDEE")]
        monkeypatch.setattr(slcan_mod, "SlcanTcpBus", FakeBus)
        monkeypatch.setattr(raw_terminal.can, "Notifier", FakeNotifier)
        monkeypatch.setattr(
            raw_terminal.isotp,
            "NotifierBasedCanStack",
            lambda bus, notifier, address=None, params=None: RetryStack(address._txid, responses),
        )
        monkeypatch.setattr(raw_terminal.time, "sleep", lambda *_a: None)
        t = raw_terminal.RawTerminal("h", 3333, 500000, timeout=0.3)
        await t.set_header(0x770)
        r = await t.send_uds("22BC03", retries=1)
        assert r["ok"] is True
        assert r["hex"] == "62BC03FDEE"
        await t.close()


class TestRawTerminalSendCommand:
    @pytest.mark.asyncio
    async def test_at_command_is_noop_ok(self, make_terminal):
        t = make_terminal({})
        await t.set_header(0x770)
        assert await t.send_command("ATSH770") == "OK"
        await t.close()

    @pytest.mark.asyncio
    async def test_uds_command_sends(self, make_terminal):
        t = make_terminal({(0x770, bytes.fromhex("3E00")): bytes.fromhex("7E00")})
        await t.set_header(0x770)
        assert await t.send_command("3E00") == "7E00"
        await t.close()


class TestRawTerminalSession:
    """enter_extended_session must accept a `mode` kwarg like WiCANTerminal and
    emit 10<mode> (regression: RawTerminal previously hardcoded 1003 and rejected
    `mode=`, breaking `scan --session` on the default slcan-tcp transport)."""

    @pytest.mark.asyncio
    async def test_default_mode_sends_1003(self, make_terminal):
        t = make_terminal({(0x7E4, bytes.fromhex("1003")): bytes.fromhex("500300320032")})
        await t.set_header(0x7E4)
        ok, task = await t.enter_extended_session()
        assert ok is True
        if task:
            task.cancel()
        await t.close()

    @pytest.mark.asyncio
    async def test_kwp_mode_81_sends_1081(self, make_terminal):
        # BMS rejects 10 03 and needs the KWP2000 standardDiagnosticSession (10 81).
        t = make_terminal({(0x7E4, bytes.fromhex("1081")): bytes.fromhex("50810032")})
        await t.set_header(0x7E4)
        ok, task = await t.enter_extended_session(mode="81")
        assert ok is True
        if task:
            task.cancel()
        await t.close()

    @pytest.mark.asyncio
    async def test_mode_is_normalized(self, make_terminal):
        # "0x03"/"3" normalize to the same 1003 request as the default.
        t = make_terminal({(0x7E4, bytes.fromhex("1003")): bytes.fromhex("500300320032")})
        await t.set_header(0x7E4)
        ok, task = await t.enter_extended_session(mode="0x3")
        assert ok is True
        if task:
            task.cancel()
        await t.close()


class TestRawTerminalSafety:
    @pytest.mark.asyncio
    async def test_blocked_service_raises_without_unsafe(self, make_terminal):
        t = make_terminal({})
        await t.set_header(0x770)
        with pytest.raises(ValueError):
            await t.send_uds("2E1234AA")  # 0x2E WriteDataByIdentifier is blocked
        await t.close()

    @pytest.mark.asyncio
    async def test_blocked_service_allowed_with_unsafe(self, make_terminal, monkeypatch):
        # Unsafe mode is unified across transports: it prompts for confirmation
        # (same as WiCANTerminal). Simulate the user typing YES.
        monkeypatch.setattr("builtins.input", lambda *a: "YES")
        t = make_terminal({(0x770, bytes.fromhex("2E1234AA")): bytes.fromhex("6E1234")})
        t.unsafe = True
        await t.set_header(0x770)
        r = await t.send_uds("2E1234AA")
        assert r["ok"] is True
        await t.close()

    @pytest.mark.asyncio
    async def test_blocked_service_declined_in_unsafe_raises(self, make_terminal, monkeypatch):
        # Declining the confirmation prompt refuses the command on every transport.
        monkeypatch.setattr("builtins.input", lambda *a: "no")
        t = make_terminal({})
        t.unsafe = True
        await t.set_header(0x770)
        with pytest.raises(ValueError):
            await t.send_uds("2E1234AA")
        await t.close()


class TestRawTerminalRxResolution:
    """The ISO-TP stack's rx address comes from addr_map (per-ECU rx_id / profile
    offset), not a hardcoded tx+8 — so a non-standard RX (e.g. XPeng +0x80) works.
    """

    @pytest.fixture
    def capture_addr(self, monkeypatch):
        seen: dict[int, int] = {}

        def _addr(*a, txid=None, rxid=None, **k):
            seen[txid] = rxid

            class _A:
                _txid = txid
                _rxid = rxid

            return _A()

        monkeypatch.setattr(slcan_mod, "SlcanTcpBus", FakeBus)
        monkeypatch.setattr(raw_terminal.can, "Notifier", FakeNotifier)
        monkeypatch.setattr(raw_terminal.isotp, "Address", _addr)
        monkeypatch.setattr(
            raw_terminal.isotp,
            "NotifierBasedCanStack",
            lambda bus, notifier, address=None, params=None: FakeStack(address._txid, {}),
        )
        monkeypatch.setattr(raw_terminal.time, "sleep", lambda *_a: None)
        return seen

    def test_addr_map_used(self, capture_addr):
        from canlib.addressing import EcuAddress

        # Non-standard mapping 0x704 -> 0x784 (XPeng), and a fallback offset for
        # an address not in the map.
        t = raw_terminal.RawTerminal(
            "h", 3333, 500000, addr_map={0x704: EcuAddress(0x704, 0x784)}, rx_offset=0x80
        )
        t._stack(0x704)
        t._stack(0x710)  # unknown -> tx + rx_offset
        assert capture_addr[0x704] == 0x784
        assert capture_addr[0x710] == 0x790

    def test_default_offset_when_unconfigured(self, capture_addr):
        t = raw_terminal.RawTerminal("h", 3333, 500000)
        t._stack(0x7E4)
        assert capture_addr[0x7E4] == 0x7EC  # default +8


class TestRawTerminalModeResolution:
    """The ISO-TP stack is built for the ECU's addressing mode (11-bit vs 29-bit)."""

    @pytest.fixture
    def capture_mode(self, monkeypatch):
        from canlib.addressing import AddressingMode

        seen: list[tuple[int, int, AddressingMode]] = []

        def _build(addr):
            seen.append((addr.tx_id, addr.rx_id, addr.mode))

            class _A:
                _txid = addr.tx_id

            return _A()

        monkeypatch.setattr(slcan_mod, "SlcanTcpBus", FakeBus)
        monkeypatch.setattr(raw_terminal.can, "Notifier", FakeNotifier)
        monkeypatch.setattr(raw_terminal, "build_isotp_address", _build)
        monkeypatch.setattr(
            raw_terminal.isotp,
            "NotifierBasedCanStack",
            lambda bus, notifier, address=None, params=None: FakeStack(address._txid, {}),
        )
        monkeypatch.setattr(raw_terminal.time, "sleep", lambda *_a: None)
        return seen

    def test_per_ecu_addr_map(self, capture_mode):
        from canlib.addressing import AddressingMode, EcuAddress

        t = raw_terminal.RawTerminal(
            "h",
            3333,
            500000,
            addr_map={
                0x18DA10F1: EcuAddress(0x18DA10F1, 0x18DAF110, AddressingMode.NORMAL_FIXED_29BIT)
            },
            mode=AddressingMode.NORMAL_11BIT,
        )
        t._stack(0x18DA10F1)
        assert capture_mode[0][0] == 0x18DA10F1
        assert capture_mode[0][2] == AddressingMode.NORMAL_FIXED_29BIT

    def test_29bit_rx_derived_when_unmapped(self, capture_mode):
        from canlib.addressing import AddressingMode

        # No addr_map entry: RX for fixed-29 is the byte-swapped id.
        t = raw_terminal.RawTerminal("h", 3333, 500000, mode=AddressingMode.NORMAL_FIXED_29BIT)
        t._stack(0x18DA10F1)
        tx, rx, mode = capture_mode[0]
        assert (tx, rx, mode) == (0x18DA10F1, 0x18DAF110, AddressingMode.NORMAL_FIXED_29BIT)


class TestRawTerminalQuirk:
    def test_hk_f1xx_offset_forwarded(self, monkeypatch):
        monkeypatch.setattr(slcan_mod, "SlcanTcpBus", FakeBus)
        monkeypatch.setattr(raw_terminal.can, "Notifier", FakeNotifier)
        t = raw_terminal.RawTerminal("h", 3333, 500000, hk_f1xx_offset=True)
        assert t.hk_f1xx_offset is True
        t2 = raw_terminal.RawTerminal("h", 3333, 500000)
        assert t2.hk_f1xx_offset is False


class TestRawTerminalFcOverride:
    """_stack must thread the ECU's fc_id (from its addr_map EcuAddress) into
    build_isotp_stack (gap G-J: functional-TX / physical-RX ECUs). An ECU without
    an fc_id passes None.
    """

    @pytest.fixture
    def capture_fc(self, monkeypatch):
        seen: dict[int, int | None] = {}

        def _build(bus, notifier, address, params, *, fc_id=None):
            seen[address._txid] = fc_id
            return FakeStack(address._txid, {})

        def _addr(*a, txid=None, rxid=None, **k):
            class _A:
                _txid = txid

            return _A()

        monkeypatch.setattr(slcan_mod, "SlcanTcpBus", FakeBus)
        monkeypatch.setattr(raw_terminal.can, "Notifier", FakeNotifier)
        monkeypatch.setattr(raw_terminal.isotp, "Address", _addr)
        monkeypatch.setattr(raw_terminal, "build_isotp_stack", _build)
        monkeypatch.setattr(raw_terminal.time, "sleep", lambda *_a: None)
        return seen

    def test_fc_id_threaded_from_addr_map(self, capture_fc):
        from canlib.addressing import AddressingMode, EcuAddress

        t = raw_terminal.RawTerminal(
            "h",
            3333,
            500000,
            addr_map={
                # Renault functional-TX with a physical FC override.
                0x18DB33F1: EcuAddress(
                    0x18DB33F1, 0x18DAF1DB, AddressingMode.NORMAL_29BIT, fc_id=0x18DADBF1
                ),
                # Plain ECU: no fc_id -> None.
                0x7E4: EcuAddress(0x7E4, 0x7EC),
            },
        )
        t._stack(0x18DB33F1)
        t._stack(0x7E4)
        assert capture_fc[0x18DB33F1] == 0x18DADBF1
        assert capture_fc[0x7E4] is None

    def test_unmapped_ecu_passes_none(self, capture_fc):
        # A discovery-sweep TX id absent from addr_map has no fc_id override.
        t = raw_terminal.RawTerminal("h", 3333, 500000)
        t._stack(0x750)
        assert capture_fc[0x750] is None
