"""Tests for canair.safety.blocklist.enforce_command_safety — the shared, transport-independent guard."""

import io

import pytest

from canair.safety.blocklist import CommandDeclined, SafetyBlocked, enforce_command_safety


class _Tty(io.StringIO):
    def isatty(self) -> bool:
        return True


def _terminal(monkeypatch, typed: str) -> None:
    """Stdin is a terminal and the user types ``typed``."""
    monkeypatch.setattr("sys.stdin", _Tty(typed))


class TestEnforceCommandSafety:
    @pytest.mark.asyncio
    async def test_safe_command_passes(self):
        # No exception, no prompt.
        await enforce_command_safety("22BC03", unsafe=False)
        await enforce_command_safety("ATSH7E4", unsafe=False)

    @pytest.mark.asyncio
    async def test_blocked_command_refused_in_safe_mode(self):
        with pytest.raises(ValueError):
            await enforce_command_safety("2E1234AA", unsafe=False)

    @pytest.mark.asyncio
    async def test_blocked_command_confirmed_in_unsafe_mode(self, monkeypatch):
        _terminal(monkeypatch, "YES\n")
        # Confirmed -> returns without raising.
        await enforce_command_safety("2E1234AA", unsafe=True)

    @pytest.mark.asyncio
    async def test_blocked_command_declined_in_unsafe_mode(self, monkeypatch, capsys):
        _terminal(monkeypatch, "n\n")
        with pytest.raises(CommandDeclined):
            await enforce_command_safety("2E1234AA", unsafe=True)
        err = capsys.readouterr().err
        assert "!! BLOCKED" in err
        assert "Aborted — nothing changed." in err

    @pytest.mark.asyncio
    async def test_a_lower_case_yes_is_not_consent(self, monkeypatch):
        _terminal(monkeypatch, "yes\n")
        with pytest.raises(CommandDeclined):
            await enforce_command_safety("2E1234AA", unsafe=True)

    @pytest.mark.asyncio
    async def test_declines_on_eof(self, monkeypatch):
        _terminal(monkeypatch, "")
        with pytest.raises(CommandDeclined):
            await enforce_command_safety("3400", unsafe=True)

    @pytest.mark.asyncio
    async def test_no_terminal_is_a_refusal_not_a_prompt(self, monkeypatch):
        monkeypatch.setattr("sys.stdin", io.StringIO("YES\n"))
        with pytest.raises(SafetyBlocked, match="needs a terminal") as raised:
            await enforce_command_safety("2E1234AA", unsafe=True)
        assert not isinstance(raised.value, CommandDeclined)

    @pytest.mark.asyncio
    async def test_unsafe_cannot_broadcast_a_refused_request(self, monkeypatch):
        """The functional allow-list is never widened: --unsafe does not even prompt."""

        def _never(*a):
            raise AssertionError("prompted for a broadcast")

        monkeypatch.setattr("builtins.input", _never)
        with pytest.raises(SafetyBlocked, match="functionally"):
            await enforce_command_safety("14FFFFFF", unsafe=True, tx_id=0x7DF)

    @pytest.mark.asyncio
    async def test_unsafe_cannot_send_a_safety_system_routine(self, monkeypatch):
        """Pyrotechnic deployment (RoutineControl E200-E2FF): --unsafe does not even prompt."""
        _terminal(monkeypatch, "YES\n")
        monkeypatch.setattr("canair.safety.blocklist.typed_confirm", _never_asked)
        with pytest.raises(SafetyBlocked, match="safety-system routine") as raised:
            await enforce_command_safety("3101E201", unsafe=True)
        assert not isinstance(raised.value, CommandDeclined)

    @pytest.mark.asyncio
    async def test_unsafe_still_asks_for_control_dtc_setting_off(self, monkeypatch):
        _terminal(monkeypatch, "YES\n")
        await enforce_command_safety("8502", unsafe=True)


def _never_asked(*_args):
    raise AssertionError("prompted for a request --unsafe may not lift")
