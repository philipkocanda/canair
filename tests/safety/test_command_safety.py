"""Tests for check_command_safety — write/session-mode blocklist hardening."""

from __future__ import annotations

import pytest

from canair.safety.blocklist import (
    SessionPolicy,
    check_command_safety,
    check_session_mode,
    safety_system_block,
    session_mode_block,
    unliftable_block,
)
from canair.uds.protocol import KWP, UDS, UNKNOWN, classify_protocol

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
    from canair.safety.quirks import NISSAN_C0_SESSION, allowed_session_modes

    meta = {"quirks": [NISSAN_C0_SESSION]}
    assert allowed_session_modes(meta, KWP) == {0xC0}
    assert allowed_session_modes(meta, UDS) == frozenset()
    assert allowed_session_modes(meta, UNKNOWN) == frozenset()
    assert allowed_session_modes({"quirks": ["skm_wakeup"]}, KWP) == frozenset()
    assert allowed_session_modes(None, KWP) == frozenset()


def test_hk_quirk_allows_only_the_90_session_and_only_on_kwp():
    from canair.safety.quirks import HK_90_SESSION, allowed_session_modes

    meta = {"quirks": [HK_90_SESSION]}
    assert allowed_session_modes(meta, KWP) == {0x90}
    assert allowed_session_modes(meta, UDS) == frozenset()
    assert allowed_session_modes(meta, UNKNOWN) == frozenset()
    policy = SessionPolicy({KWP_ECU: KWP, UDS_ECU: UDS}, {KWP: frozenset({0x90})})
    assert check_command_safety("1090") is not None
    assert check_command_safety("1090", policy, KWP_ECU) is None
    # On a UDS ECU 0x90 is OEM session 0x10 with the suppress bit.
    assert check_command_safety("1090", policy, UDS_ECU) is not None


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
    assert classify_protocol("KWP2000") == KWP
    assert classify_protocol("kwp") == KWP
    assert classify_protocol("UDS") == UDS
    for value in (None, "", "none", "unknown", 3):
        assert classify_protocol(value) == UNKNOWN


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
        assert check_command_safety("A") is None  # single hex char

    def test_text_that_is_neither_hex_nor_at_is_refused(self):
        """What the guard cannot read it does not let through (an STN STPX sends any frame)."""
        assert "BLOCKED" in check_command_safety("hello")
        assert "BLOCKED" in check_command_safety("STPX H:7E0,D:2EF19000")

    @pytest.mark.parametrize("cmd", ["10\t02", "10\n02", "2E\tF1\n90 00", " 11\r01 "])
    def test_any_whitespace_is_judged_as_the_transport_sends_it(self, cmd):
        """bytes.fromhex skips tabs and newlines too, so the guard must not be fooled by them."""
        assert bytes.fromhex(cmd)  # the bytes a raw transport would put on the bus
        assert "BLOCKED" in check_command_safety(cmd)


class TestBroadcastRequests:
    """Any request to an unregistered functional id must be an allowed functional read."""

    @pytest.mark.parametrize("tx", [0x7DF, 0x18DB33F1])
    @pytest.mark.parametrize("cmd", ["14FFFFFF", "04", "8502", "1003", "2FBC1003", "3101FF00"])
    def test_a_state_change_cannot_be_broadcast(self, tx, cmd):
        assert "may not be sent functionally" in check_command_safety(cmd, SessionPolicy(), tx)

    @pytest.mark.parametrize("cmd", ["14FFFFFF", "04", "8501", "1003"])
    def test_the_same_request_sent_physically_is_not_refused(self, cmd):
        assert check_command_safety(cmd, SessionPolicy(), 0x7E0) is None

    @pytest.mark.parametrize("cmd", ["3E00", "22F190", "0100", "1A87", "190208"])
    def test_an_allowed_read_may_be_broadcast(self, cmd):
        assert check_command_safety(cmd, SessionPolicy(), 0x7DF) is None

    def test_the_elm327_frame_count_digit_is_not_part_of_the_request(self):
        assert check_command_safety("3E001", SessionPolicy(), 0x7DF) is None
        assert check_command_safety("14FFFFFF1", SessionPolicy(), 0x7DF) is not None

    def test_a_registered_functional_tx_ecu_is_addressed_like_any_other(self):
        policy = SessionPolicy({0x7DF: UDS})
        assert check_command_safety("1003", policy, 0x7DF) is None
        assert check_command_safety("1002", policy, 0x7DF) is not None

    def test_without_a_policy_the_broadcast_rule_applies(self):
        assert check_command_safety("14FFFFFF", None, 0x7DF) is not None


# ── check_command_safety: service and routine blocklist ──────────────────────


@pytest.mark.parametrize(
    "cmd",
    [
        "1101",  # ECUReset
        "2802",  # CommunicationControl: disable normal message transmission
        "3B9000",  # KWP2000 WriteDataByLocalIdentifier
        "3D14000010FF",  # WriteMemoryByAddress
        "38000100",  # KWP2000 StartRoutineByAddress (runs code at a memory address)
        "39000100",  # KWP2000 StopRoutineByAddress
        "870101",  # LinkControl
        "2901",  # Authentication: verifyCertificateUnidirectional
        "2C01F300F19001000002",  # DynamicallyDefineDataIdentifier: defineByIdentifier
        "2C03F300",  # DynamicallyDefineDataIdentifier: clear
        "84000000",  # SecuredDataTransmission: wraps a request the guard cannot read
    ],
)
def test_state_changing_services_blocked(cmd):
    assert check_command_safety(cmd) is not None


@pytest.mark.parametrize(
    "cmd",
    [
        "8502",  # ControlDTCSetting off
        "8582",  # ... with the suppress-positive-response bit
        "8503",  # an unknown DTCSettingType
        "85",  # no sub-function: the guard cannot tell what it does
        "8302",  # AccessTimingParameter: setTimingParametersToDefaultValues
        "830400320032",  # AccessTimingParameter: setTimingParametersToGivenValues
        "2A01F2",  # ReadDataByPeriodicIdentifier: sendAtSlowRate
        "2A03F2",  # ... sendAtFastRate
        "8601",  # ResponseOnEvent: onDTCStatusChange
        "8605",  # ResponseOnEvent: startResponseOnEvent
        "8644",  # reportActivatedEvents with the storageState bit is another request
    ],
)
def test_guarded_services_refuse_what_changes_state(cmd):
    blocked = check_command_safety(cmd, POLICY, UDS_ECU)
    assert blocked is not None and "--unsafe" in blocked


@pytest.mark.parametrize(
    "cmd",
    [
        "8501",  # ControlDTCSetting on: restores the default
        "8581",  # ... with the suppress-positive-response bit
        "8301",  # AccessTimingParameter: readExtendedTimingParameterSet
        "8383",  # readCurrentlyActiveTimingParameters, suppress bit
        "2A04F2",  # ReadDataByPeriodicIdentifier: stopSending
        "2A04",  # stopSending for every periodic identifier
        "8600",  # ResponseOnEvent: stopResponseOnEvent
        "8604",  # ResponseOnEvent: reportActivatedEvents
        "85011",  # an ELM327 request with its frame-count digit
    ],
)
def test_guarded_services_pass_what_reads_or_undoes(cmd):
    assert check_command_safety(cmd, POLICY, UDS_ECU) is None


def test_periodic_transmission_mode_has_no_suppress_bit():
    # 0x84 is not stopSending with the suppress bit: transmissionMode is not a sub-function.
    assert check_command_safety("2A84F2", POLICY, UDS_ECU) is not None


def test_guarded_service_rule_holds_on_kwp2000():
    # ISO 14230-3 ControlDTCSetting uses the same DTCSettingType values.
    assert check_command_safety("8502", POLICY, KWP_ECU) is not None
    assert check_command_safety("8501", POLICY, KWP_ECU) is None


@pytest.mark.parametrize(
    "cmd", ["3101FF00", "3181FF00", "3102FF01", "31010212", "31010411", "3101FF02"]
)
def test_reprogramming_routines_blocked(cmd):
    blocked = check_command_safety(cmd, POLICY, UDS_ECU)
    assert blocked is not None and "--unsafe" in blocked


class TestSafetySystemRoutines:
    """ISO 14229-1 E200-E2FF: pyrotechnic deployment, refused with or without --unsafe."""

    @pytest.mark.parametrize("cmd", ["3101E200", "3101E201", "3181E201", "3102E2FF", "3101E2420A"])
    @pytest.mark.parametrize("tx", [UDS_ECU, 0x7FF])  # UDS, and unknown (stricter)
    def test_start_and_stop_are_refused(self, cmd, tx):
        blocked = check_command_safety(cmd, POLICY, tx)
        assert blocked is not None and "safety-system routine" in blocked
        assert safety_system_block(cmd, POLICY, tx) == blocked
        assert unliftable_block(cmd, POLICY, tx) == blocked

    def test_the_elm327_frame_count_digit_does_not_hide_it(self):
        assert safety_system_block("3101E2011", POLICY, UDS_ECU) is not None

    @pytest.mark.parametrize("cmd", ["3103E201", "3101E1FF", "3101E300"])
    def test_results_and_neighbouring_ids_are_not_safety_routines(self, cmd):
        assert safety_system_block(cmd, POLICY, UDS_ECU) is None

    def test_a_kwp2000_local_identifier_is_not_judged(self):
        # KWP2000 31 01 E201 starts local routine 0x01 with option bytes E2 01.
        assert safety_system_block("3101E201", POLICY, KWP_ECU) is None
        assert check_command_safety("3102E201", POLICY, KWP_ECU) is None


class TestKwpRoutines:
    """KWP2000 StartRoutineByLocalIdentifier ids that enter the bootloader (Hyundai/Kia OBC)."""

    @pytest.mark.parametrize("cmd", ["3100", "310000", "310100", "3101E201", "3100003"])
    def test_bootloader_entries_are_refused_on_kwp(self, cmd):
        blocked = check_command_safety(cmd, POLICY, KWP_ECU)
        assert blocked is not None and "bootloader" in blocked and "--unsafe" in blocked

    @pytest.mark.parametrize("cmd", ["3102", "3111", "31F1EEFF0011", "31"])
    def test_other_local_ids_pass(self, cmd):
        assert check_command_safety(cmd, POLICY, KWP_ECU) is None

    @pytest.mark.parametrize("tx", [UDS_ECU, 0x7FF])
    def test_a_uds_or_unknown_ecu_is_judged_as_routine_control(self, tx):
        # UDS 31 01 F000 starts routine F000, not local id 0x01.
        assert check_command_safety("3101F000", POLICY, tx) is None


@pytest.mark.parametrize("tx", [KWP_ECU, UDS_ECU, 0x7FF])
@pytest.mark.parametrize("cmd", ["99", "9900", "99FF00"])
def test_the_manufacturer_service_99_is_refused(cmd, tx):
    blocked = check_command_safety(cmd, POLICY, tx)
    assert blocked is not None and "0x99" in blocked

    def test_an_ordinary_blocked_routine_stays_liftable(self):
        assert unliftable_block("3101FF00", POLICY, UDS_ECU) is None


def test_kwp_routine_results_by_address_is_a_read():
    assert check_command_safety("3A000100", POLICY, KWP_ECU) is None


def test_routine_results_and_other_routines_are_allowed():
    assert check_command_safety("3103FF00", POLICY, UDS_ECU) is None  # requestRoutineResults
    assert check_command_safety("310112A1", POLICY, UDS_ECU) is None  # an ordinary OEM routine
    assert check_command_safety("3101F000", POLICY, UDS_ECU) is None


def test_routine_rule_skips_kwp_local_identifiers():
    # KWP2000 31 02 FF00 starts local routine 0x02 with option bytes FF 00, not UDS FF00.
    assert check_command_safety("3102FF00", POLICY, KWP_ECU) is None
    assert check_command_safety("3102FF01", POLICY, 0x7FF) is not None  # unknown: stricter


class TestActionGrants:
    """Exact grants: how a guarded profile action sends one request the blocklist refuses."""

    FUNCTIONAL = 0x7DF

    def test_the_sleep_request_is_refused_without_a_grant(self):
        assert check_command_safety("10D1", SessionPolicy(), self.FUNCTIONAL) is not None

    def test_a_grant_lifts_exactly_that_request_on_that_id(self):
        policy = SessionPolicy().granting([(self.FUNCTIONAL, "10 d1")])
        assert check_command_safety("10D1", policy, self.FUNCTIONAL) is None
        assert check_command_safety("10 D1", policy, self.FUNCTIONAL) is None
        # Another id, another sub-function, or another service stay refused.
        assert check_command_safety("10D1", policy, 0x770) is not None
        assert check_command_safety("10D0", policy, self.FUNCTIONAL) is not None
        assert check_command_safety("10D1FF", policy, self.FUNCTIONAL) is not None

    def test_a_grant_cannot_lift_a_hard_block_or_a_blocked_service(self):
        policy = SessionPolicy().granting(
            [(self.FUNCTIONAL, "1002"), (self.FUNCTIONAL, "1082"), (self.FUNCTIONAL, "1101")]
        )
        assert check_command_safety("1002", policy, self.FUNCTIONAL) is not None
        assert check_command_safety("1082", policy, self.FUNCTIONAL) is not None
        assert check_command_safety("1101", policy, self.FUNCTIONAL) is not None

    def test_the_quirk_grants_nothing_to_the_ordinary_policy(self):
        """hk_forced_sleep is not a session allowance: read/raw steps still refuse 10 D1."""
        data = {"ecus": {}, "quirks": ["hk_forced_sleep"]}
        assert check_command_safety("10D1", SessionPolicy.from_profile(data), self.FUNCTIONAL)

    def test_the_quirk_declares_only_the_functional_sleep_request(self):
        from canair.safety.quirks import action_grants

        assert action_grants({"quirks": ["hk_forced_sleep"]}) == {(self.FUNCTIONAL, "10D1")}
        assert action_grants({"quirks": ["hk_90_session"]}) == frozenset()
        assert action_grants(None) == frozenset()
