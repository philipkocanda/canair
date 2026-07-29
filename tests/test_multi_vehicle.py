"""Phase 3/4 multi-vehicle support: 29-bit discovery, SLCAN extended frames,
padding-byte + quirk resolution.

See plans/2026-07-28-multi-vehicle-support.md (Phases 3–4).
"""

import can
import isotp

from canlib.addressing import AddressingMode, EcuAddress, build_isotp_address
from canlib.modes.discover import discovery_targets, fmt_id
from canlib.quirks import HK_F1XX_MINUS_ONE, has_quirk, resolve_quirks
from canlib.transport.isotp_params import DEFAULT_TX_PADDING, resolve_tx_padding
from canlib.transport.slcan_tcp import format_slcan_frame


class TestDiscoveryTargets:
    def test_11bit_sweeps_ids_directly(self):
        assert discovery_targets((0x700, 0x702)) == [0x700, 0x701, 0x702]

    def test_29bit_forms_18da_request_ids(self):
        # target bytes 0x10..0x11 with tester 0xF1 -> 0x18DA{target}{tester}.
        ids = discovery_targets((0x10, 0x11), AddressingMode.NORMAL_FIXED_29BIT, tester=0xF1)
        assert ids == [0x18DA10F1, 0x18DA11F1]

    def test_29bit_custom_tester(self):
        ids = discovery_targets((0x07, 0x07), AddressingMode.NORMAL_FIXED_29BIT, tester=0xFA)
        assert ids == [0x18DA07FA]


class TestFmtId:
    def test_11bit(self):
        assert fmt_id(0x7E4) == "0x7E4"

    def test_29bit(self):
        assert fmt_id(0x18DA10F1) == "0x18DA10F1"


class TestSlcanExtendedFrame:
    """29-bit ISO-TP frames must transmit as SLCAN extended (uppercase T) frames."""

    def test_isotp_emits_extended_frame_for_29bit(self):
        sent: list[can.Message] = []

        class _Bus(can.BusABC):
            def __init__(self):
                self.channel_info = "fake"
                super().__init__(channel="x")

            def send(self, msg, timeout=None):
                sent.append(msg)

            def _recv_internal(self, timeout):
                return None, False

            def shutdown(self):
                pass

        bus = _Bus()
        addr = build_isotp_address(
            EcuAddress(0x18DA10F1, 0x18DAF110, AddressingMode.NORMAL_FIXED_29BIT)
        )
        stack = isotp.CanStack(bus, address=addr)
        stack.send(bytes.fromhex("221101"))
        stack.process()
        bus.shutdown()
        assert sent, "expected a frame to be transmitted"
        msg = sent[0]
        assert msg.is_extended_id is True
        assert msg.arbitration_id == 0x18DA10F1

    def test_format_slcan_extended(self):
        msg = can.Message(
            arbitration_id=0x18DA10F1,
            is_extended_id=True,
            data=bytes.fromhex("03221101AAAAAAAA"),
            dlc=8,
        )
        frame = format_slcan_frame(msg)
        assert frame.startswith("T18DA10F1")  # uppercase T = extended id

    def test_format_slcan_standard(self):
        msg = can.Message(
            arbitration_id=0x7E4,
            is_extended_id=False,
            data=bytes.fromhex("0322110100000000"),
            dlc=8,
        )
        assert format_slcan_frame(msg).startswith("t7E4")  # lowercase t = 11-bit


class TestQuirks:
    def test_default_no_quirks(self):
        assert resolve_quirks(None) == frozenset()
        assert resolve_quirks({}) == frozenset()

    def test_declared_quirk(self):
        meta = {"quirks": ["hk_f1xx_minus_one"]}
        assert has_quirk(meta, HK_F1XX_MINUS_ONE) is True

    def test_undeclared_quirk(self):
        assert has_quirk({"quirks": []}, HK_F1XX_MINUS_ONE) is False

    def test_malformed_block(self):
        assert resolve_quirks({"quirks": "nope"}) == frozenset()


class TestResolveTxPadding:
    def test_default(self):
        assert resolve_tx_padding(None) == DEFAULT_TX_PADDING
        assert resolve_tx_padding({}) == 0xAA

    def test_from_isotp_block(self):
        assert resolve_tx_padding({"isotp": {"tx_padding": 0x00}}) == 0x00
        assert resolve_tx_padding({"isotp": {"tx_padding": 0xCC}}) == 0xCC

    def test_bool_rejected(self):
        assert resolve_tx_padding({"isotp": {"tx_padding": True}}) == 0xAA

    def test_out_of_range_rejected(self):
        assert resolve_tx_padding({"isotp": {"tx_padding": 999}}) == 0xAA
