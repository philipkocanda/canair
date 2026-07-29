"""Tests for canlib.addressing — TX→RX response-address resolution.

Covers the Phase 2 addressing abstraction: an explicit per-ECU rx_id, a
profile-level addressing.rx_offset (e.g. the XPeng G6's +0x80), and the
conventional +0x08 default — plus that the ECU-index/registry builders and the
RX↔TX lookups all honor it (see plans/2026-07-28-multi-vehicle-support.md).
"""

from canlib.addressing import (
    DEFAULT_RX_OFFSET,
    AddressingMode,
    build_isotp_address,
    fixed_29bit_rx,
    is_extended,
    parse_mode,
    resolve_mode,
    resolve_rx,
    resolve_rx_offset,
)
from canlib.ecus import build_rx_index, build_rx_tx_index, resolve_tx, rx_for_tx
from canlib.pids import build_ecu_index


class TestResolveRx:
    def test_default_offset(self):
        assert resolve_rx(0x7E4) == 0x7EC
        assert resolve_rx(0x770) == 0x778

    def test_explicit_rx_id_wins(self):
        # XPeng G6: request 0x704, response 0x784 (not +8).
        assert resolve_rx(0x704, rx_id=0x784) == 0x784

    def test_explicit_rx_id_wins_over_offset(self):
        assert resolve_rx(0x704, rx_id=0x784, rx_offset=0x80) == 0x784

    def test_custom_offset(self):
        assert resolve_rx(0x704, rx_offset=0x80) == 0x784

    def test_default_constant(self):
        assert DEFAULT_RX_OFFSET == 0x08


class TestResolveRxOffset:
    def test_none(self):
        assert resolve_rx_offset(None) == 0x08

    def test_empty(self):
        assert resolve_rx_offset({}) == 0x08

    def test_declared(self):
        assert resolve_rx_offset({"addressing": {"rx_offset": 0x80}}) == 0x80

    def test_bool_rejected(self):
        # bool is an int subclass — must not be read as an offset.
        assert resolve_rx_offset({"addressing": {"rx_offset": True}}) == 0x08

    def test_malformed_block(self):
        assert resolve_rx_offset({"addressing": "nope"}) == 0x08


class TestBuildEcuIndexRx:
    def test_default_offset(self):
        data = {"ecus": {"BMS": {"tx_id": 0x7E4, "pids": {}}}}
        idx = build_ecu_index(data)
        assert idx["BMS"]["rx_id"] == 0x7EC

    def test_profile_offset(self):
        data = {"addressing": {"rx_offset": 0x80}, "ecus": {"VCU": {"tx_id": 0x704, "pids": {}}}}
        idx = build_ecu_index(data)
        assert idx["VCU"]["rx_id"] == 0x784

    def test_per_ecu_override_beats_profile_offset(self):
        data = {
            "addressing": {"rx_offset": 0x80},
            "ecus": {"ODD": {"tx_id": 0x700, "rx_id": 0x7A0, "pids": {}}},
        }
        idx = build_ecu_index(data)
        assert idx["ODD"]["rx_id"] == 0x7A0


class TestRxLookupsHonorResolution:
    def _ecus(self):
        # Shape mirrors canlib.ecus.load_ecus output (keyed by tx, rx_id resolved).
        return {
            0x7E4: {"name": "BMS", "rx_id": 0x7EC},
            0x704: {"name": "VCU", "rx_id": 0x784},  # non-standard +0x80
        }

    def test_rx_for_tx_known(self):
        ecus = self._ecus()
        assert rx_for_tx(0x704, ecus) == 0x784
        assert rx_for_tx(0x7E4, ecus) == 0x7EC

    def test_rx_for_tx_unknown_uses_offset(self):
        assert rx_for_tx(0x750, {}, rx_offset=0x80) == 0x7D0
        assert rx_for_tx(0x750, {}) == 0x758  # default +8

    def test_build_rx_index(self):
        idx = build_rx_index(self._ecus())
        assert idx == {0x7EC: "BMS", 0x784: "VCU"}

    def test_build_rx_tx_index(self):
        idx = build_rx_tx_index(self._ecus())
        assert idx == {0x7EC: 0x7E4, 0x784: 0x704}

    def test_resolve_tx_from_nonstandard_rx(self):
        ecus = self._ecus()
        # 0x784 is VCU's response addr -> resolves back to its TX 0x704.
        assert resolve_tx("0x784", name_index={}, ecus=ecus) == 0x704
        # A known TX passes straight through.
        assert resolve_tx("0x704", name_index={}, ecus=ecus) == 0x704


class TestAddressingMode:
    """Phase 3: the 11-bit/29-bit addressing-mode vocabulary + resolution."""

    def test_parse_mode(self):
        assert parse_mode("normal_11bit") == AddressingMode.NORMAL_11BIT
        assert parse_mode("NORMAL_FIXED_29BIT") == AddressingMode.NORMAL_FIXED_29BIT
        assert parse_mode(AddressingMode.NORMAL_29BIT) == AddressingMode.NORMAL_29BIT
        assert parse_mode("bogus") is None
        assert parse_mode(None) is None

    def test_is_extended(self):
        assert not is_extended(AddressingMode.NORMAL_11BIT)
        assert is_extended(AddressingMode.NORMAL_29BIT)
        assert is_extended(AddressingMode.NORMAL_FIXED_29BIT)
        assert is_extended(AddressingMode.EXTENDED_29BIT)

    def test_resolve_mode_default(self):
        assert resolve_mode(None) == AddressingMode.NORMAL_11BIT
        assert resolve_mode({}) == AddressingMode.NORMAL_11BIT

    def test_resolve_mode_profile(self):
        meta = {"addressing": {"mode": "normal_fixed_29bit"}}
        assert resolve_mode(meta) == AddressingMode.NORMAL_FIXED_29BIT

    def test_resolve_mode_per_ecu_overrides_profile(self):
        meta = {"addressing": {"mode": "normal_11bit"}}
        ecu = {"addressing": {"mode": "normal_fixed_29bit"}}
        assert resolve_mode(meta, ecu) == AddressingMode.NORMAL_FIXED_29BIT

    def test_resolve_mode_ignores_bad_value(self):
        assert resolve_mode({"addressing": {"mode": "nope"}}) == AddressingMode.NORMAL_11BIT

    def test_fixed_29bit_rx_swaps_bytes(self):
        # 0x18DA{target}{tester} request -> 0x18DA{tester}{target} response.
        assert fixed_29bit_rx(0x18DA10F1) == 0x18DAF110
        assert fixed_29bit_rx(0x18DA00F1) == 0x18DAF100

    def test_resolve_rx_fixed_29bit(self):
        assert resolve_rx(0x18DA10F1, mode=AddressingMode.NORMAL_FIXED_29BIT) == 0x18DAF110

    def test_resolve_rx_explicit_wins_over_29bit(self):
        assert (
            resolve_rx(0x18DA10F1, rx_id=0x18DAF199, mode=AddressingMode.NORMAL_FIXED_29BIT)
            == 0x18DAF199
        )


class TestBuildIsotpAddress:
    """Phase 3: (tx, rx, mode) -> isotp.Address for both raw clients."""

    def test_normal_11bit(self):
        a = build_isotp_address(0x7E4, 0x7EC, AddressingMode.NORMAL_11BIT)
        assert a.get_tx_arbitration_id() == 0x7E4
        assert a.get_rx_arbitration_id() == 0x7EC

    def test_normal_29bit_arbitrary(self):
        a = build_isotp_address(0x18DA10F1, 0x18DAF110, AddressingMode.NORMAL_29BIT)
        assert a.get_tx_arbitration_id() == 0x18DA10F1
        assert a.get_rx_arbitration_id() == 0x18DAF110

    def test_normal_fixed_29bit(self):
        # target/source extracted from the request id -> 18DA convention.
        a = build_isotp_address(0x18DA10F1, 0x18DAF110, AddressingMode.NORMAL_FIXED_29BIT)
        assert a.get_tx_arbitration_id() == 0x18DA10F1
        assert a.get_rx_arbitration_id() == 0x18DAF110

    def test_extended_29bit(self):
        a = build_isotp_address(0x18DA10F1, 0x18DAF110, AddressingMode.EXTENDED_29BIT)
        assert a.get_tx_arbitration_id() == 0x18DA10F1


class TestRegistryModeResolution:
    """build_ecu_index / load_ecus store each ECU's resolved addressing mode + RX."""

    def test_default_mode(self):
        idx = build_ecu_index({"ecus": {"BMS": {"tx_id": 0x7E4, "pids": {}}}})
        assert idx["BMS"]["mode"] == "normal_11bit"
        assert idx["BMS"]["rx_id"] == 0x7EC

    def test_profile_29bit_mode(self):
        data = {
            "addressing": {"mode": "normal_fixed_29bit"},
            "ecus": {"PCM": {"tx_id": 0x18DA10F1, "pids": {}}},
        }
        idx = build_ecu_index(data)
        assert idx["PCM"]["mode"] == "normal_fixed_29bit"
        assert idx["PCM"]["rx_id"] == 0x18DAF110

    def test_per_ecu_mode_override(self):
        data = {
            "addressing": {"mode": "normal_11bit"},
            "ecus": {
                "PCM": {
                    "tx_id": 0x18DA10F1,
                    "addressing": {"mode": "normal_fixed_29bit"},
                    "pids": {},
                }
            },
        }
        idx = build_ecu_index(data)
        assert idx["PCM"]["mode"] == "normal_fixed_29bit"
        assert idx["PCM"]["rx_id"] == 0x18DAF110
