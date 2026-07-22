"""Tests for canlib.safety.enforce_command_safety — the shared, transport-independent guard."""

import pytest

from canlib.safety import enforce_command_safety


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
        monkeypatch.setattr("builtins.input", lambda *a: "YES")
        # Confirmed -> returns without raising.
        await enforce_command_safety("2E1234AA", unsafe=True)

    @pytest.mark.asyncio
    async def test_blocked_command_declined_in_unsafe_mode(self, monkeypatch):
        monkeypatch.setattr("builtins.input", lambda *a: "n")
        with pytest.raises(ValueError):
            await enforce_command_safety("2E1234AA", unsafe=True)

    @pytest.mark.asyncio
    async def test_declines_on_eof(self, monkeypatch):
        def _raise(*a):
            raise EOFError

        monkeypatch.setattr("builtins.input", _raise)
        with pytest.raises(ValueError):
            await enforce_command_safety("3400", unsafe=True)
