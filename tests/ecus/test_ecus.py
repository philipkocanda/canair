"""Tests for canair.ecus — ECU address lookup and RX resolution."""

import pytest

from canair.ecus.names import (
    EcuNameCollision,
    build_alias_index,
    build_canonical_name_index,
    build_name_tx_index,
    canonical_ecu_name,
    canonical_ecu_name_safe,
    ecu_display,
    ecu_name,
    resolve_tx,
    rx_from_name,
)
from canair.ecus.registry import (
    build_rx_index,
    derive_identity_confidence,
    ecu_identity_confidence,
    ecu_name_from_ref,
    load_ecus,
    parse_ecu_ref,
    rx_addr_str,
)
from canair.profile import active


@pytest.fixture(scope="module")
def ecus():
    return load_ecus(profile=active())


class TestRxAddrStr:
    def test_bms(self):
        assert rx_addr_str(0x7E4, profile=active()) == "0x7EC"

    def test_igpm(self):
        assert rx_addr_str(0x770, profile=active()) == "0x778"

    def test_zero_padded_three_digits(self):
        assert rx_addr_str(0x001, profile=active()) == "0x009"


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
        idx = build_rx_index(ecus, profile=active())
        assert idx[0x7EC] == "BMS"

    def test_igpm_resolves(self, ecus):
        idx = build_rx_index(ecus, profile=active())
        assert idx[0x778] == "IGPM"

    def test_covers_all_ecus(self, ecus):
        idx = build_rx_index(ecus, profile=active())
        assert len(idx) == len(ecus)


class TestEcuNameFromRef:
    def test_address_resolves_to_name(self):
        assert ecu_name_from_ref("0x7EC", profile=active()) == "BMS"

    def test_sentinel_passthrough(self):
        assert ecu_name_from_ref("broadcast", profile=active()) == "broadcast"

    def test_unknown_address_passthrough(self):
        # 0x001 + not a real responder -> returned verbatim
        assert ecu_name_from_ref("0x001", profile=active()) == "0x001"


class TestBuildNameTxIndex:
    def test_canonical_name(self, ecus):
        idx = build_name_tx_index(ecus, profile=active())
        assert idx["BMS"] == 0x7E4

    def test_alias_resolves(self, ecus):
        idx = build_name_tx_index(ecus, profile=active())
        # SKM self-identifies as SMK (alias)
        assert idx["SMK"] == idx["SKM"]

    def test_alias_name_collision_raises(self):
        # An alias that clashes with a *different* ECU's name is ambiguous.
        ecus = {0x700: {"name": "FOO", "alias": "BAR"}, 0x708: {"name": "BAR"}}
        with pytest.raises(EcuNameCollision):
            build_name_tx_index(ecus, profile=active())

    def test_duplicate_alias_collision_raises(self):
        ecus = {
            0x700: {"name": "FOO", "alias": "X"},
            0x708: {"name": "BAZ", "alias": "X"},
        }
        with pytest.raises(EcuNameCollision):
            build_name_tx_index(ecus, profile=active())

    def test_self_alias_is_allowed(self):
        # An alias equal to the ECU's own name is not a collision.
        ecus = {0x700: {"name": "FOO", "alias": "FOO"}}
        assert build_name_tx_index(ecus, profile=active())["FOO"] == 0x700


class TestCanonicalEcuName:
    def test_alias_resolves_to_primary(self):
        assert canonical_ecu_name("SMK", profile=active()) == "SKM"
        assert canonical_ecu_name("MDPS", profile=active()) == "EPS"
        assert canonical_ecu_name("AHB", profile=active()) == "ESC"

    def test_primary_name_passthrough(self):
        assert canonical_ecu_name("BMS", profile=active()) == "BMS"

    def test_case_insensitive(self):
        assert canonical_ecu_name("smk", profile=active()) == "SKM"

    def test_unknown_returns_upper_unchanged(self):
        assert canonical_ecu_name("NOPE", profile=active()) == "NOPE"
        assert canonical_ecu_name("nope", profile=active()) == "NOPE"

    def test_none_and_empty(self):
        assert canonical_ecu_name(None, profile=active()) == ""
        assert canonical_ecu_name("", profile=active()) == ""

    def test_build_index_maps_alias_and_name(self, ecus):
        idx = build_canonical_name_index(ecus, profile=active())
        assert idx["SMK"] == "SKM"
        assert idx["SKM"] == "SKM"
        assert idx["BMS"] == "BMS"

    def test_build_alias_index(self, ecus):
        aliases = build_alias_index(ecus, profile=active())
        # SKM self-identifies as SMK in the bundled profile.
        assert aliases.get("SKM") == "SMK"
        # An ECU without an alias is absent (not mapped to itself/empty).
        assert "BMS" not in aliases or aliases["BMS"]


class TestCanonicalEcuNameSafe:
    def test_alias_resolves_to_primary(self):
        assert canonical_ecu_name_safe("LDC", profile=active()) == "OBC"
        assert canonical_ecu_name_safe("smk", profile=active()) == "SKM"

    def test_primary_and_unknown_passthrough(self):
        assert canonical_ecu_name_safe("BMS", profile=active()) == "BMS"
        assert canonical_ecu_name_safe("nope", profile=active()) == "NOPE"

    def test_none_and_empty(self):
        assert canonical_ecu_name_safe(None, profile=active()) == ""
        assert canonical_ecu_name_safe("", profile=active()) == ""

    def test_missing_registry_falls_back_to_upper(self, monkeypatch):
        # A profile may ship pids/ without ecus.yaml: degrade to the raw name
        # instead of crashing (unlike bare canonical_ecu_name).
        from canair.ecus import names as ecus_mod_names

        def _raise(*a, **k):
            raise FileNotFoundError("ecus.yaml")

        monkeypatch.setattr(ecus_mod_names, "canonical_ecu_name", _raise)
        assert canonical_ecu_name_safe("ldc", profile=active()) == "LDC"
        assert canonical_ecu_name_safe(None, profile=active()) == ""


class TestRxFromName:
    def test_name(self):
        assert rx_from_name("BMS", profile=active()) == "0x7EC"

    def test_alias(self):
        assert rx_from_name("SMK", profile=active()) == rx_from_name("SKM", profile=active())

    def test_case_insensitive(self):
        assert rx_from_name("bms", profile=active()) == "0x7EC"

    def test_unknown_is_none(self):
        assert rx_from_name("NOPE", profile=active()) is None


class TestEcuName:
    def test_known(self, ecus):
        assert ecu_name(0x7E4, ecus, profile=active()) == "BMS"

    def test_unknown_falls_back_to_hex(self, ecus):
        assert ecu_name(0x123, ecus, profile=active()) == "0x123"


class TestResolveTx:
    def test_name(self):
        assert resolve_tx("BMS", profile=active()) == 0x7E4

    def test_name_case_insensitive(self):
        assert resolve_tx("bms", profile=active()) == 0x7E4

    def test_alias(self):
        assert resolve_tx("SMK", profile=active()) == resolve_tx("SKM", profile=active())

    def test_hex_with_prefix(self):
        assert resolve_tx("0x770", profile=active()) == 0x770

    def test_hex_without_prefix(self):
        assert resolve_tx("7E4", profile=active()) == 0x7E4

    def test_int_passthrough(self):
        assert resolve_tx(0x7E4, profile=active()) == 0x7E4

    def test_rx_addr_resolves_to_tx(self):
        # VCU: TX 0x7E2, RX 0x7EA (= TX + 8) — the RX addr resolves to the TX id.
        assert resolve_tx("0x7EA", profile=active()) == 0x7E2
        assert resolve_tx("7EA", profile=active()) == 0x7E2

    def test_known_tx_wins_over_rx_interpretation(self):
        # BMS TX 0x7E4 is itself a registered TX; it must resolve to itself, not
        # be reinterpreted as some other ECU's RX.
        assert resolve_tx("0x7E4", profile=active()) == 0x7E4

    def test_unknown_name_is_none(self):
        assert resolve_tx("NOPE", profile=active()) is None

    def test_unknown_hex_passthrough(self):
        # An unregistered hex that is neither a TX nor an RX is returned as-is.
        assert resolve_tx("0x123", profile=active()) == 0x123

    def test_empty_is_none(self):
        assert resolve_tx("", profile=active()) is None
        assert resolve_tx(None, profile=active()) is None


class TestEcuDisplay:
    def test_known(self, ecus):
        assert ecu_display(0x7E4, ecus, profile=active()) == "BMS (0x7E4)"

    def test_unknown_falls_back_to_hex(self, ecus):
        assert ecu_display(0x123, ecus, profile=active()) == "0x123"


class TestIdentityConfidence:
    def test_part_number_is_confirmed(self):
        assert (
            derive_identity_confidence({"name": "PLC", "part_number": "91950G7200"}) == "confirmed"
        )

    def test_identity_fields_uds_is_probable(self):
        info = {"name": "AVN", "id_protocol": "UDS", "app_sw": "AE_EV EURSOP 12 017.1"}
        assert derive_identity_confidence(info) == "probable"

    def test_cross_vehicle_note_is_tentative(self):
        info = {
            "name": "AMP",
            "id_protocol": "none",
            "notes": "Identified via Kia e-Niro CAN spreadsheet (exact address match 783/78B).",
        }
        assert derive_identity_confidence(info) == "tentative"

    def test_unknown_name_is_speculative(self):
        assert derive_identity_confidence({"name": "Unknown-746"}) == "speculative"

    def test_no_evidence_defaults_tentative(self):
        assert derive_identity_confidence({"name": "FOO", "id_protocol": "none"}) == "tentative"

    def test_explicit_overrides_derivation(self):
        info = {"name": "PLC", "part_number": "91950G7200", "identity_confidence": "tentative"}
        value, explicit = ecu_identity_confidence(info)
        assert value == "tentative"
        assert explicit is True

    def test_derived_when_not_set(self):
        value, explicit = ecu_identity_confidence({"name": "PLC", "part_number": "91950G7200"})
        assert value == "confirmed"
        assert explicit is False
