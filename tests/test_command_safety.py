"""Tests for check_command_safety — write/session-mode blocklist hardening."""

from __future__ import annotations

import pytest

from canlib.safety import (
    KWP,
    UDS,
    UNKNOWN,
    SessionPolicy,
    check_command_safety,
    check_session_mode,
    session_mode_block,
    session_protocol,
)

KWP_ECU, UDS_ECU = 0x7E4, 0x770
POLICY = SessionPolicy({KWP_ECU: KWP, UDS_ECU: UDS})

# ── check_command_safety: session-mode hardening ─────────────────────────────


def test_programming_sessions_blocked():
    assert check_command_safety("1002") is not None  # UDS programmingSession
    assert check_command_safety("1085") is not None  # KWP2000 ECU programming mode


@pytest.mark.parametrize("protocol", [UDS, KWP, UNKNOWN])
def test_programming_sessions_blocked_on_every_protocol(protocol):
    # Either reading of a misdeclared id_protocol must stay refused.
    assert check_session_mode(0x02, protocol) is not None
    assert check_session_mode(0x85, protocol) is not None


def test_unknown_kwp_session_modes_blocked():
    # 0x8x band that isn't a known-safe KWP2000 session → blocked (may be prog/dev mode).
    for cmd in ("1090", "1086", "10FF"):
        assert check_command_safety(cmd) is not None
        assert check_command_safety(cmd, POLICY, KWP_ECU) is not None


def test_safe_sessions_allowed():
    assert check_command_safety("1001") is None  # default
    assert check_command_safety("1003") is None  # UDS extended
    assert check_command_safety("1081") is None  # KWP standard / UDS default + suppress
    assert check_command_safety("1083") is None  # KWP extended / UDS extended + suppress
    assert check_command_safety("1082", POLICY, KWP_ECU) is None  # KWP periodic/EOL diagnostic


def test_82_is_programming_on_uds_and_when_the_protocol_is_unknown():
    """Regression: 0x82 was allowed everywhere, but on UDS it is 0x02 + suppress bit."""
    uds = check_command_safety("1082", POLICY, UDS_ECU)
    assert uds is not None and "programmingSession" in uds
    # No policy, an unregistered id, or a None target: the stricter reading wins.
    assert check_command_safety("1082") is not None
    assert check_command_safety("1082", POLICY, 0x7FF) is not None
    assert check_command_safety("1082", POLICY, None) is not None


def test_uds_judges_the_session_type_without_the_suppress_bit():
    assert check_session_mode(0x81, UDS) is None
    assert check_session_mode(0x83, UDS) is None
    assert check_session_mode(0x82, UDS) is not None
    # The suppress-bit form of a reserved session serves no read-only purpose.
    assert check_session_mode(0x90, UDS) is not None


def test_uds_safety_system_session_is_hard_blocked():
    for sub in (0x04, 0x84):
        block = session_mode_block(sub, UDS, allowed={0x04})
        assert block is not None and block.hard
        assert "safetySystem" in block.reason
    assert session_mode_block(0x04, KWP) is not None
    assert session_mode_block(0x04, UNKNOWN) is not None


@pytest.mark.parametrize("sub", [0x40, 0x5F, 0x60, 0x7E, 0xC0, 0xFE])
def test_uds_oem_and_supplier_band_blocked_unless_allowed(sub):
    block = session_mode_block(sub, UDS)
    assert block is not None and not block.hard
    assert session_mode_block(sub, UDS, allowed={sub & 0x7F}) is None


def test_uds_reserved_session_types_are_not_blocked():
    # ISO-reserved types get an NRC from a conformant ECU; nothing to guard.
    assert check_session_mode(0x10, UDS) is None
    assert check_session_mode(0x06, UDS) is None


@pytest.mark.parametrize("protocol", [UDS, KWP, UNKNOWN])
def test_hyundai_kia_programming_mode_05_is_hard_blocked(protocol):
    """Regression: ES95486 implements the ISO-reserved 0x05 as an alias of programmingSession."""
    block = session_mode_block(0x05, protocol, allowed={0x05})
    assert block is not None and block.hard
    assert check_command_safety("1005", POLICY, UDS_ECU) is not None


def test_oem_session_blocked_unless_the_profile_allows_it():
    kwp_c0 = SessionPolicy({KWP_ECU: KWP}, {KWP: frozenset({0xC0})})
    assert check_command_safety("10C0") is not None
    assert check_command_safety("10C0", kwp_c0, KWP_ECU) is None
    # The allowance is exact: it does not open the rest of the OEM band.
    assert check_command_safety("10C1", kwp_c0, KWP_ECU) is not None


def test_a_kwp_allowance_never_reaches_a_uds_or_unknown_ecu():
    policy = SessionPolicy({KWP_ECU: KWP, UDS_ECU: UDS}, {KWP: frozenset({0xC0})})
    assert check_command_safety("10C0", policy, UDS_ECU) is not None
    assert check_command_safety("10C0", policy, 0x7FF) is not None


def test_programming_sessions_cannot_be_allowed():
    for protocol in (UDS, KWP, UNKNOWN):
        assert check_session_mode(0x02, protocol, {0x02}) is not None
        assert check_session_mode(0x85, protocol, {0x85}) is not None
        assert check_session_mode(0x82, UDS, {0x82, 0x02}) is not None


def test_nissan_quirk_allows_only_the_c0_session_and_only_on_kwp():
    from canlib.quirks import NISSAN_C0_SESSION, allowed_session_modes

    meta = {"quirks": [NISSAN_C0_SESSION]}
    assert allowed_session_modes(meta, KWP) == {0xC0}
    assert allowed_session_modes(meta, UDS) == frozenset()
    assert allowed_session_modes(meta, UNKNOWN) == frozenset()
    assert allowed_session_modes({"quirks": ["hk_f1xx_minus_one"]}, KWP) == frozenset()
    assert allowed_session_modes(None, KWP) == frozenset()


class TestSessionPolicyFromProfile:
    def _profile(self, quirks=None):
        data = {
            "ecus": {
                "BMS": {"tx_id": 0x7E4, "identity": {"id_protocol": "KWP2000"}},
                "IGPM": {"tx_id": 0x770, "identity": {"id_protocol": "UDS"}},
                "ODD": {"tx_id": 0x7A0},
            }
        }
        if quirks:
            data["quirks"] = quirks
        return data

    def test_protocols_come_from_id_protocol(self):
        policy = SessionPolicy.from_profile(self._profile())
        assert policy.protocol(0x7E4) == KWP
        assert policy.protocol(0x770) == UDS
        assert policy.protocol(0x7A0) == UNKNOWN
        assert policy.protocol(None) == UNKNOWN

    def test_a_shared_tx_id_with_mixed_protocols_is_unknown(self):
        data = self._profile()
        data["ecus"]["TWIN"] = {"tx_id": 0x770, "identity": {"id_protocol": "KWP2000"}}
        assert SessionPolicy.from_profile(data).protocol(0x770) == UNKNOWN

    def test_the_nissan_quirk_is_scoped_to_kwp_ecus(self):
        policy = SessionPolicy.from_profile(self._profile(["nissan_c0_session"]))
        assert policy.check(0xC0, 0x7E4) is None
        assert policy.check(0xC0, 0x770) is not None
        assert policy.check(0xC0, 0x7A0) is not None

    def test_safe_modes_filters_a_candidate_list(self):
        policy = SessionPolicy.from_profile(self._profile())
        cands = ("01", "03", "81", "82", "83")
        assert policy.safe_modes(0x7E4, cands) == list(cands)
        assert policy.safe_modes(0x770, cands) == ["01", "03", "81", "83"]


def test_session_protocol_classification():
    assert session_protocol("KWP2000") == KWP
    assert session_protocol("kwp") == KWP
    assert session_protocol("UDS") == UDS
    for value in (None, "", "none", "unknown", 3):
        assert session_protocol(value) == UNKNOWN


class TestCheckCommandSafety:
    def test_at_commands_always_safe(self):
        assert check_command_safety("ATSP6") is None
        assert check_command_safety("ATSH7E4") is None
        assert check_command_safety("atst96") is None

    def test_read_services_allowed(self):
        assert check_command_safety("2101") is None  # ReadDataByLocalId
        assert check_command_safety("22BC03") is None  # ReadDataByIdentifier
        assert check_command_safety("1001") is None  # DiagSession default
        assert check_command_safety("1003") is None  # DiagSession extended

    def test_blocked_write_services(self):
        assert "BLOCKED" in check_command_safety("2E F187 00")
        assert "BLOCKED" in check_command_safety("3400")
        assert "BLOCKED" in check_command_safety("35")
        assert "BLOCKED" in check_command_safety("3601AABB")

    def test_blocked_programming_session(self):
        result = check_command_safety("1002")
        assert result is not None
        assert "programmingSession" in result

    def test_iocontrol_allowed(self):
        # 0x2F IOControl is NOT in blocked list (deliberate — used for testing)
        assert check_command_safety("2FBC1003") is None

    def test_empty_and_nonsense(self):
        assert check_command_safety("") is None
        assert check_command_safety("hello") is None
        assert check_command_safety("A") is None  # single hex char


# ── check_command_safety: service and routine blocklist ──────────────────────


@pytest.mark.parametrize(
    "cmd",
    [
        "1101",  # ECUReset
        "2802",  # CommunicationControl: disable normal message transmission
        "3B9000",  # KWP2000 WriteDataByLocalIdentifier
        "3D14000010FF",  # WriteMemoryByAddress
        "870101",  # LinkControl
    ],
)
def test_state_changing_services_blocked(cmd):
    assert check_command_safety(cmd) is not None


@pytest.mark.parametrize("cmd", ["3101FF00", "3181FF00", "3102FF01", "31010212", "31010411"])
def test_reprogramming_routines_blocked(cmd):
    blocked = check_command_safety(cmd, POLICY, UDS_ECU)
    assert blocked is not None and "--unsafe" in blocked


def test_routine_results_and_other_routines_are_allowed():
    assert check_command_safety("3103FF00", POLICY, UDS_ECU) is None  # requestRoutineResults
    assert check_command_safety("310112A1", POLICY, UDS_ECU) is None  # an ordinary OEM routine
    assert check_command_safety("3101F000", POLICY, UDS_ECU) is None


def test_routine_rule_skips_kwp_local_identifiers():
    # KWP2000 31 01 FF00 starts local routine 0x01 with option bytes FF 00.
    assert check_command_safety("3101FF00", POLICY, KWP_ECU) is None
    assert check_command_safety("3101FF00", POLICY, 0x7FF) is not None  # unknown: stricter
