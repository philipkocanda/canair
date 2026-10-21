"""Tests for check_command_safety — write/session-mode blocklist hardening."""

from __future__ import annotations

from canlib.safety import check_command_safety

# ── check_command_safety: KWP session-mode hardening ─────────────────────────


def test_programming_sessions_blocked():
    assert check_command_safety("1002") is not None  # UDS programmingSession
    assert check_command_safety("1085") is not None  # KWP2000 ECU programming mode


def test_unknown_kwp_session_modes_blocked():
    # 0x8x band that isn't the known-safe 0x81 → blocked (may be prog/dev mode).
    assert check_command_safety("1090") is not None
    assert check_command_safety("1086") is not None
    assert check_command_safety("10FF") is not None


def test_safe_sessions_allowed():
    assert check_command_safety("1001") is None  # default
    assert check_command_safety("1003") is None  # UDS extended
    assert check_command_safety("1081") is None  # KWP standard
    assert check_command_safety("1082") is None  # KWP periodic/EOL diagnostic
    assert check_command_safety("1083") is None  # KWP extended diagnostic


def test_oem_session_blocked_unless_the_profile_allows_it():
    assert check_command_safety("10C0") is not None
    assert check_command_safety("10C0", allowed_sessions={0xC0}) is None
    # The allowance is exact: it does not open the rest of the OEM band.
    assert check_command_safety("10C1", allowed_sessions={0xC0}) is not None


def test_programming_sessions_cannot_be_allowed():
    assert check_command_safety("1002", allowed_sessions={0x02}) is not None
    assert check_command_safety("1085", allowed_sessions={0x85}) is not None


def test_nissan_quirk_allows_only_the_c0_session():
    from canlib.quirks import NISSAN_C0_SESSION, allowed_session_modes

    assert allowed_session_modes({"quirks": [NISSAN_C0_SESSION]}) == {0xC0}
    assert allowed_session_modes({"quirks": ["hk_f1xx_minus_one"]}) == frozenset()
    assert allowed_session_modes(None) == frozenset()


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
