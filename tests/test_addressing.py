"""Tests for canlib.addressing — TX→RX response-address resolution.

Covers the Phase 2 addressing abstraction: an explicit per-ECU rx_id, a
profile-level addressing.rx_offset (e.g. the XPeng G6's +0x80), and the
conventional +0x08 default — plus that the ECU-index/registry builders and the
RX↔TX lookups all honor it (see plans/2026-07-28-multi-vehicle-support.md).
"""

from canlib.addressing import DEFAULT_RX_OFFSET, resolve_rx, resolve_rx_offset
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
