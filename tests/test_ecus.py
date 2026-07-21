"""Tests for canlib.ecus — ECU address lookup and RX resolution."""

import pytest

from canlib.ecus import (
    build_name_tx_index,
    build_rx_index,
    ecu_display,
    ecu_name,
    ecu_name_from_ref,
    load_ecus,
    parse_ecu_ref,
    resolve_tx,
    rx_addr_str,
    rx_from_name,
)


@pytest.fixture(scope="module")
def ecus():
    return load_ecus()


class TestRxAddrStr:
    def test_bms(self):
        assert rx_addr_str(0x7E4) == "0x7EC"

    def test_igpm(self):
        assert rx_addr_str(0x770) == "0x778"

    def test_zero_padded_three_digits(self):
        assert rx_addr_str(0x001) == "0x009"


class TestParseEcuRef:
    def test_hex_with_prefix(self):
        assert parse_ecu_ref("0x7EC") == 0x7EC

    def test_hex_without_prefix(self):
        assert parse_ecu_ref("7EC") == 0x7EC

    def test_int_passthrough(self):
        assert parse_ecu_ref(0x7EC) == 0x7EC

    def test_sentinel_is_none(self):
        assert parse_ecu_ref("broadcast") is None

    def test_empty_is_none(self):
        assert parse_ecu_ref("") is None
        assert parse_ecu_ref(None) is None

    def test_garbage_is_none(self):
        assert parse_ecu_ref("not-hex") is None


class TestBuildRxIndex:
    def test_bms_resolves(self, ecus):
        idx = build_rx_index(ecus)
        assert idx[0x7EC] == "BMS"

    def test_igpm_resolves(self, ecus):
        idx = build_rx_index(ecus)
        assert idx[0x778] == "IGPM"

    def test_covers_all_ecus(self, ecus):
        idx = build_rx_index(ecus)
        assert len(idx) == len(ecus)


class TestEcuNameFromRef:
    def test_address_resolves_to_name(self):
        assert ecu_name_from_ref("0x7EC") == "BMS"

    def test_sentinel_passthrough(self):
        assert ecu_name_from_ref("broadcast") == "broadcast"

    def test_unknown_address_passthrough(self):
        # 0x001 + not a real responder -> returned verbatim
        assert ecu_name_from_ref("0x001") == "0x001"


class TestBuildNameTxIndex:
    def test_canonical_name(self, ecus):
        idx = build_name_tx_index(ecus)
        assert idx["BMS"] == 0x7E4

    def test_alias_resolves(self, ecus):
        idx = build_name_tx_index(ecus)
        # SKM self-identifies as SMK (alias)
        assert idx["SMK"] == idx["SKM"]


class TestRxFromName:
    def test_name(self):
        assert rx_from_name("BMS") == "0x7EC"

    def test_alias(self):
        assert rx_from_name("SMK") == rx_from_name("SKM")

    def test_case_insensitive(self):
        assert rx_from_name("bms") == "0x7EC"

    def test_unknown_is_none(self):
        assert rx_from_name("NOPE") is None


class TestEcuName:
    def test_known(self, ecus):
        assert ecu_name(0x7E4, ecus) == "BMS"

    def test_unknown_falls_back_to_hex(self, ecus):
        assert ecu_name(0x123, ecus) == "0x123"


class TestResolveTx:
    def test_name(self):
        assert resolve_tx("BMS") == 0x7E4

    def test_name_case_insensitive(self):
        assert resolve_tx("bms") == 0x7E4

    def test_alias(self):
        assert resolve_tx("SMK") == resolve_tx("SKM")

    def test_hex_with_prefix(self):
        assert resolve_tx("0x770") == 0x770

    def test_hex_without_prefix(self):
        assert resolve_tx("7E4") == 0x7E4

    def test_int_passthrough(self):
        assert resolve_tx(0x7E4) == 0x7E4

    def test_unknown_name_is_none(self):
        assert resolve_tx("NOPE") is None

    def test_empty_is_none(self):
        assert resolve_tx("") is None
        assert resolve_tx(None) is None


class TestEcuDisplay:
    def test_known(self, ecus):
        assert ecu_display(0x7E4, ecus) == "BMS (0x7E4)"

    def test_unknown_falls_back_to_hex(self, ecus):
        assert ecu_display(0x123, ecus) == "0x123"
