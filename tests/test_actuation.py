"""Tests for canlib.actuation: the read / release / actuate request classifier."""

from __future__ import annotations

import pytest

from canlib.actuation import (
    ACTUATE,
    READ,
    RELEASE,
    classify,
    default_release,
    normalize_request,
    on_refusal,
    parse_io_request,
    release_mismatch,
)
from canlib.pids import build_iocontrol_index
from canlib.profile import ProfileError
from canlib.safety import KWP, UDS, UNKNOWN

PROTOCOLS = (UDS, KWP, UNKNOWN)


class TestClassify:
    @pytest.mark.parametrize(
        "request_hex",
        ["0100", "0902", "1003", "1081", "1902FF", "1A87", "2101", "22F190", "2701", "3E00"],
    )
    @pytest.mark.parametrize("protocol", PROTOCOLS)
    def test_reads(self, request_hex, protocol):
        assert classify(request_hex, protocol) == READ

    @pytest.mark.parametrize(
        "request_hex", ["04", "14FFFFFF", "1101", "2801", "2EF19001", "3B0101", "8502", "BA01"]
    )
    @pytest.mark.parametrize("protocol", PROTOCOLS)
    def test_state_changing_and_unknown_services_actuate(self, request_hex, protocol):
        assert classify(request_hex, protocol) == ACTUATE

    @pytest.mark.parametrize("protocol", (UDS, UNKNOWN))
    def test_uds_return_control_is_a_release(self, protocol):
        assert classify("2FBC0100", protocol) == RELEASE
        for option in ("01", "02", "03"):
            assert classify(f"2FBC01{option}", protocol) == ACTUATE

    def test_2f_00_on_a_kwp_ecu_is_not_assumed_to_release(self):
        assert classify("2FBC0100", KWP) == ACTUATE

    @pytest.mark.parametrize("protocol", PROTOCOLS)
    def test_kwp_0x30_actuates_unless_declared(self, protocol):
        # The 0x30 option byte is manufacturer-defined; no option byte is a
        # release by itself.
        for request_hex in ("300100", "300111", "30070000"):
            assert classify(request_hex, protocol) == ACTUATE
        assert classify("300111", protocol, releases=["30 01 11"]) == RELEASE

    def test_routine_control_depends_on_the_protocol(self):
        assert classify("3103F000", UDS) == READ
        assert classify("3102F000", UDS) == RELEASE
        assert classify("3101F000", UDS) == ACTUATE
        # KWP2000 0x31 is StartRoutineByLocalIdentifier; unknown gets the stricter one.
        for protocol in (KWP, UNKNOWN):
            assert classify("3103", protocol) == ACTUATE
            assert classify("3102", protocol) == ACTUATE

    def test_kwp_routine_stop_and_results(self):
        assert classify("3301", KWP) == READ
        assert classify("3201", KWP) == RELEASE
        assert classify("20", KWP) == RELEASE
        assert classify("3201", UDS) == ACTUATE

    def test_at_commands_are_reads_and_spacing_is_tolerated(self):
        assert classify("ATSH7E4", KWP) == READ
        assert classify("2f bc 01 00", UDS) == RELEASE

    def test_empty_actuates_and_garbage_raises(self):
        assert classify("", UDS) == ACTUATE
        with pytest.raises(ValueError):
            classify("lock", UDS)


class TestIoRequest:
    def test_uds_layout(self):
        io = parse_io_request("2FBC0103FF")
        assert io is not None
        assert (io.service, io.identifier, io.option, io.state) == (0x2F, b"\xbc\x01", 3, b"\xff")

    def test_kwp_layout_has_a_one_byte_local_identifier(self):
        io = parse_io_request("30070001")
        assert io is not None
        assert (io.service, io.identifier, io.option, io.state) == (0x30, b"\x07", 0, b"\x01")

    def test_other_services_and_short_requests(self):
        assert parse_io_request("22F190") is None
        assert parse_io_request("2FBC") is None
        assert parse_io_request("3007").option is None  # type: ignore[union-attr]


class TestRelease:
    def test_uds_implies_return_control_on_the_same_did(self):
        assert default_release("2FBC0103", UDS) == "2FBC0100"
        assert default_release("2FBC0103", UNKNOWN) == "2FBC0100"

    def test_kwp_implies_nothing(self):
        assert default_release("300100", KWP) == ""
        assert default_release("300100", UDS) == ""
        assert default_release("2FBC0103", KWP) == ""
        assert default_release("not hex", UDS) == ""

    def test_mismatch(self):
        assert release_mismatch("300100", "300111") is None
        assert "addresses 30 02" in (release_mismatch("300100", "300211") or "")
        assert "ON request itself" in (release_mismatch("300100", "30 01 00") or "")

    def test_on_refusal(self):
        assert on_refusal("2FBC0103", "2FBC0100", UDS) is None
        assert on_refusal("300100", "300111", KWP) is None
        assert "no release declared" in (on_refusal("300100", "", KWP) or "")
        assert "ON request itself" in (on_refusal("300100", "300100", KWP) or "")
        # Nothing to refuse: no ON, or an ON that is itself a release.
        assert on_refusal("", "", KWP) is None
        assert on_refusal("2FBC0100", "2FBC0100", UDS) is None
        assert "not a hex request" in (on_refusal("lock", "", UDS) or "")

    def test_normalize(self):
        assert normalize_request("30 01 0a") == "30010A"


def _data(identity_protocol, iocontrol, discoveries=None):
    ecu = {"tx_id": 0x7E5, "identity": {"id_protocol": identity_protocol}, "iocontrol": iocontrol}
    if discoveries:
        ecu["iocontrol_discoveries"] = discoveries
    return {"ecus": {"OBC": ecu}}


class TestIndexRelease:
    def test_uds_entries_derive_their_release(self):
        data = _data("UDS", {"BC01": {"on": "2FBC0103", "off": "2FBC0100"}})
        entry = build_iocontrol_index(data)["OBC"]
        assert entry["protocol"] == UDS
        assert entry["cmds"]["BC01"]["release"] == "2FBC0100"

    def test_a_value_only_uds_entry_derives_its_release_from_the_key(self):
        data = _data("UDS", {"F012": {"on": "", "off": ""}})
        assert build_iocontrol_index(data)["OBC"]["cmds"]["F012"]["release"] == "2FF01200"

    def test_kwp_entries_only_have_a_declared_release(self):
        data = _data(
            "KWP2000",
            {
                "01": {"on": "300100", "off": "300111"},
                "02": {"on": "300200", "off": "300211", "release": "30 02 11"},
            },
        )
        cmds = build_iocontrol_index(data)["OBC"]["cmds"]
        assert cmds["01"]["release"] == ""
        assert cmds["02"]["release"] == "300211"

    def test_discoveries(self):
        data = _data("KWP2000", {}, discoveries={"0A": {}})
        cmds = build_iocontrol_index(data, include_discoveries=True)["OBC"]["cmds"]
        assert cmds["0A"]["release"] == ""
        data = _data("UDS", {}, discoveries={"BC40": {}})
        cmds = build_iocontrol_index(data, include_discoveries=True)["OBC"]["cmds"]
        assert cmds["BC40"]["release"] == "2FBC4000"

    def test_an_unquoted_or_non_hex_release_is_a_profile_error(self):
        for bad in (300111, "off"):
            data = _data("KWP2000", {"01": {"on": "300100", "release": bad}})
            with pytest.raises(ProfileError, match="OBC/iocontrol/01"):
                build_iocontrol_index(data)
