"""Tests for canlib.addressing — TX→RX response-address resolution.

Covers the Phase 2 addressing abstraction: an explicit per-ECU rx_id, a
profile-level addressing.rx_offset (e.g. the XPeng G6's +0x80), and the
conventional +0x08 default — plus that the ECU-index/registry builders and the
RX↔TX lookups all honor it (see plans/2026-07-28-multi-vehicle-support.md).
"""

from canlib.addressing import (
    DEFAULT_RX_OFFSET,
    DEFAULT_TESTER_ADDRESS,
    AddressingMode,
    EcuAddress,
    build_isotp_address,
    fixed_29bit_rx,
    is_extended,
    parse_mode,
    resolve_ecu_address,
    resolve_fc_id,
    resolve_mode,
    resolve_rx,
    resolve_rx_offset,
    resolve_source_address,
    resolve_target_address,
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
    """Phase 3: EcuAddress -> isotp.Address for both raw clients."""

    def test_normal_11bit(self):
        a = build_isotp_address(EcuAddress(0x7E4, 0x7EC, AddressingMode.NORMAL_11BIT))
        assert a.get_tx_arbitration_id() == 0x7E4
        assert a.get_rx_arbitration_id() == 0x7EC

    def test_normal_29bit_arbitrary(self):
        a = build_isotp_address(EcuAddress(0x18DA10F1, 0x18DAF110, AddressingMode.NORMAL_29BIT))
        assert a.get_tx_arbitration_id() == 0x18DA10F1
        assert a.get_rx_arbitration_id() == 0x18DAF110

    def test_normal_fixed_29bit(self):
        # target/source extracted from the request id -> 18DA convention.
        a = build_isotp_address(
            EcuAddress(0x18DA10F1, 0x18DAF110, AddressingMode.NORMAL_FIXED_29BIT)
        )
        assert a.get_tx_arbitration_id() == 0x18DA10F1
        assert a.get_rx_arbitration_id() == 0x18DAF110

    def test_extended_29bit(self):
        a = build_isotp_address(EcuAddress(0x18DA10F1, 0x18DAF110, AddressingMode.EXTENDED_29BIT))
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


class TestNegativeRxOffset:
    """Gap G-L: PSA/Stellantis use a negative offset (0x6B4 -> 0x694 = -0x20)."""

    def test_resolve_rx_offset_negative(self):
        assert resolve_rx_offset({"addressing": {"rx_offset": -0x20}}) == -0x20

    def test_resolve_rx_negative_offset(self):
        assert resolve_rx(0x6B4, rx_offset=-0x20) == 0x694
        assert resolve_rx(0x6A2, rx_offset=-0x20) == 0x682

    def test_registry_negative_offset(self):
        data = {"addressing": {"rx_offset": -0x20}, "ecus": {"BSI": {"tx_id": 0x6A6, "pids": {}}}}
        idx = build_ecu_index(data)
        assert idx["BSI"]["rx_id"] == 0x686


class TestNon0x18Priority:
    """Gap G-K: non-0x18 29-bit priority + non-derivable RX via normal_29bit ids.

    The escape hatch is arbitrary explicit tx_id/rx_id under normal_29bit — GM
    Global-A (0x14...), VW MEB (0x17...), Volvo SPA (0x1D...) all fit there because
    the ids are baked in whole (priority included), no derivation attempted.
    """

    def test_gm_global_a_ids_pass_through(self):
        # GM: TX 0x14DACBF1 -> RX 0x142AF1CB (DA->2A discriminator, not a byte-swap).
        a = build_isotp_address(EcuAddress(0x14DACBF1, 0x142AF1CB, AddressingMode.NORMAL_29BIT))
        assert a.get_tx_arbitration_id() == 0x14DACBF1
        assert a.get_rx_arbitration_id() == 0x142AF1CB

    def test_vw_meb_ids_pass_through(self):
        a = build_isotp_address(EcuAddress(0x17FC007B, 0x17FE007B, AddressingMode.NORMAL_29BIT))
        assert a.get_tx_arbitration_id() == 0x17FC007B
        assert a.get_rx_arbitration_id() == 0x17FE007B

    def test_registry_resolves_arbitrary_29bit(self):
        # normal_29bit keeps the explicit rx_id verbatim (no fixed-29 byte-swap).
        data = {
            "ecus": {
                "PCM": {
                    "tx_id": 0x1DD01635,
                    "rx_id": 0x1EC6AE80,
                    "addressing": {"mode": "normal_29bit"},
                    "pids": {},
                }
            }
        }
        idx = build_ecu_index(data)
        assert idx["PCM"]["rx_id"] == 0x1EC6AE80


class TestEcuAddressResolution:
    """resolve_ecu_address bundles mode + RX + extended/FC bytes."""

    def test_plain_11bit(self):
        addr = resolve_ecu_address(None, {"tx_id": 0x7E4})
        assert addr == EcuAddress(0x7E4, 0x7EC, AddressingMode.NORMAL_11BIT)

    def test_extended_11bit_defaults_tester(self):
        # Gap G-I: BMW extended-11-bit — target from the ECU, source defaults 0xF1.
        meta = {"addressing": {"mode": "normal_extended_11bit"}}
        ecu = {"tx_id": 0x6F1, "rx_id": 0x612, "addressing": {"target_address": 0x12}}
        addr = resolve_ecu_address(meta, ecu)
        assert addr.mode == AddressingMode.NORMAL_EXTENDED_11BIT
        assert addr.rx_id == 0x612
        assert addr.target_address == 0x12
        assert addr.source_address == DEFAULT_TESTER_ADDRESS

    def test_functional_tx_fc_id(self):
        # Gap G-J: Renault functional-TX with a physical FC override.
        ecu = {
            "tx_id": 0x18DB33F1,
            "rx_id": 0x18DAF1DB,
            "addressing": {"mode": "normal_29bit", "fc_id": 0x18DADBF1},
        }
        addr = resolve_ecu_address(None, ecu)
        assert addr.fc_id == 0x18DADBF1
        assert addr.rx_id == 0x18DAF1DB

    def test_target_source_precedence_per_ecu_over_profile(self):
        meta = {"addressing": {"source_address": 0xF0, "target_address": 0x01}}
        ecu = {"tx_id": 0x6F1, "addressing": {"target_address": 0x60}}
        assert resolve_target_address(meta, ecu) == 0x60  # per-ECU wins
        assert resolve_source_address(meta, ecu) == 0xF0  # profile default used

    def test_no_fc_id_by_default(self):
        assert resolve_fc_id(None, {"tx_id": 0x7E4}) is None


class TestExtendedAddressingIsotp:
    """Gap G-I: build_isotp_address for the extended (mixed) 11-bit scheme."""

    def test_extended_11bit_prepends_target(self):
        addr = EcuAddress(
            0x6F1,
            0x612,
            AddressingMode.NORMAL_EXTENDED_11BIT,
            target_address=0x12,
            source_address=0xF1,
        )
        a = build_isotp_address(addr)
        assert a.get_tx_arbitration_id() == 0x6F1
        assert a.get_rx_arbitration_id() == 0x612
        # The target-address extension byte rides as the first payload byte.
        assert a.get_tx_payload_prefix() == b"\x12"

    def test_extended_11bit_missing_target_raises(self):
        import pytest

        addr = EcuAddress(0x6F1, 0x612, AddressingMode.NORMAL_EXTENDED_11BIT)
        with pytest.raises(ValueError, match="target_address"):
            build_isotp_address(addr)
