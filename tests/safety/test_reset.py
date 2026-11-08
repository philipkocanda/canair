"""ECUReset stays blocked; ``canair reset``'s exact grant lifts only the resets its rule allows."""

from __future__ import annotations

import asyncio

import pytest

from canair.safety.blocklist import (
    SafetyBlocked,
    SessionPolicy,
    check_command_safety,
    enforce_command_safety,
)
from canair.safety.reset import RESET_TYPES, reset_block, reset_granted
from canair.uds.addressing import FUNCTIONAL_11BIT_ID
from canair.uds.protocol import KWP, UDS, UNKNOWN
from canair.uds.subfunctions import EcuResetType

UDS_ECU, KWP_ECU, BARE_ECU = 0x770, 0x7E4, 0x7A0
POLICY = SessionPolicy({UDS_ECU: UDS, KWP_ECU: KWP})


def _granted(tx_id: int, request: str) -> SessionPolicy:
    return POLICY.granting({(tx_id, request)})


@pytest.mark.parametrize("request_hex", ["1101", "1102", "1103", "1104", "1181", "11"])
def test_every_reset_is_blocked_without_a_grant(request_hex):
    assert "ECUReset" in (check_command_safety(request_hex, POLICY, UDS_ECU) or "")


@pytest.mark.parametrize("request_hex", ["1101", "1102", "1103"])
def test_a_grant_lifts_a_reset_type_on_a_uds_ecu(request_hex):
    assert check_command_safety(request_hex, _granted(UDS_ECU, request_hex), UDS_ECU) is None


def test_the_grant_is_exact():
    policy = _granted(UDS_ECU, "1101")
    assert check_command_safety("1103", policy, UDS_ECU) is not None
    assert check_command_safety("1101", policy, KWP_ECU) is not None
    assert check_command_safety("2EF19000", policy, UDS_ECU) is not None


def test_an_elm327_frame_count_digit_does_not_hide_the_grant():
    assert check_command_safety("11011", _granted(UDS_ECU, "1101"), UDS_ECU) is None


@pytest.mark.parametrize("request_hex", ["1104", "1105", "1140", "1181", "1183"])
def test_a_grant_never_lifts_what_is_not_a_plain_restart(request_hex):
    assert check_command_safety(request_hex, _granted(UDS_ECU, request_hex), UDS_ECU) is not None


@pytest.mark.parametrize("tx_id", [KWP_ECU, BARE_ECU])
def test_a_non_uds_ecu_takes_only_hard(tx_id):
    assert check_command_safety("1101", _granted(tx_id, "1101"), tx_id) is None
    for request_hex in ("1102", "1103"):
        assert check_command_safety(request_hex, _granted(tx_id, request_hex), tx_id) is not None


def test_a_functional_reset_is_refused_even_when_granted():
    policy = _granted(FUNCTIONAL_11BIT_ID, "1101")
    assert check_command_safety("1101", policy, FUNCTIONAL_11BIT_ID) is not None
    assert not reset_granted("1101", policy, FUNCTIONAL_11BIT_ID)


def test_unsafe_mode_cannot_send_a_refused_reset_without_a_terminal():
    with pytest.raises(SafetyBlocked):
        asyncio.run(enforce_command_safety("1101", True, POLICY, UDS_ECU))


def test_the_rule_reports_why():
    assert "does not restart" in (reset_block(EcuResetType.ENABLE_RAPID_POWER_SHUT_DOWN, UDS, 1))
    assert "speaks KWP2000" in (reset_block(EcuResetType.SOFT_RESET, KWP, 1) or "")
    assert "no protocol" in (reset_block(EcuResetType.SOFT_RESET, UNKNOWN, 1) or "")
    assert "functional" in (reset_block(EcuResetType.HARD_RESET, UDS, None) or "")
    assert reset_block(EcuResetType.HARD_RESET, KWP, 1) is None


def test_the_cli_names_are_the_three_restarts():
    assert dict(RESET_TYPES) == {
        "hard": EcuResetType.HARD_RESET,
        "key-off-on": EcuResetType.KEY_OFF_ON_RESET,
        "soft": EcuResetType.SOFT_RESET,
    }
