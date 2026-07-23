"""Tests for discover cross-referencing + offer-to-identify.

Covers canlib.modes.discover._classify_alive and _offer_identify (the
"check results against the existing ECU list and offer to identify" path).
"""

from __future__ import annotations

import pytest

from canlib import profile
from canlib.modes import discover as disc
from canlib.pids import clear_cache


@pytest.fixture(autouse=True)
def _restore_active_profile():
    saved = profile._active
    clear_cache()
    yield
    profile._active = saved
    clear_cache()


def _mk_profile(tmp_path, seed_ecus: str | None = None):
    root = tmp_path / "prof"
    (root / "ecus").mkdir(parents=True)
    (root / "captures").mkdir()
    (root / "profile.yaml").write_text('car_model: "T"\ninit: "ATSP6;"\n')
    if seed_ecus:
        (root / "ecus" / "bms.yaml").write_text(seed_ecus)
    profile.set_active(str(root))
    clear_cache()
    return root


ALIVE = [
    (0x7E4, "positive", "5001..."),  # seeded as BMS below
    (0x7A0, "NRC 0x11", "serviceNotSupported"),  # not in registry
]

SEED_BMS = "BMS:\n  tx_id: 0x7E4\n  identity:\n    id_protocol: UDS\n"


class _FakeStdin:
    def __init__(self, tty: bool, line: str = ""):
        self._tty = tty
        self._line = line

    def isatty(self):
        return self._tty

    def readline(self):
        return self._line


class FakeTerminal:
    """Records set_header calls; returns NO DATA so identity probes finish fast."""

    def __init__(self):
        self.headers: list[int] = []

    async def set_header(self, tx_id):
        self.headers.append(tx_id)

    async def send_uds(self, cmd, timeout=None):
        return {"ok": False, "error": "NO DATA", "raw": "NO DATA"}

    async def enter_extended_session(self, wake=False):
        return None, None


class TestClassifyAlive:
    def test_splits_known_and_new(self, tmp_path):
        _mk_profile(tmp_path, seed_ecus=SEED_BMS)
        known, new = disc._classify_alive(ALIVE)
        assert [(tx, name) for tx, _, _, name in known] == [(0x7E4, "BMS")]
        assert [tx for tx, _, _, _ in new] == [0x7A0]

    def test_all_new_when_registry_empty(self, tmp_path):
        _mk_profile(tmp_path)
        known, new = disc._classify_alive(ALIVE)
        assert known == []
        assert len(new) == 2


class TestOfferIdentify:
    @pytest.mark.asyncio
    async def test_flag_runs_identity_on_each(self, tmp_path):
        _mk_profile(tmp_path, seed_ecus=SEED_BMS)
        term = FakeTerminal()
        await disc._offer_identify(term, ALIVE, identify=True, as_json=False)
        # mode_identity sets the header for every alive ECU.
        assert 0x7E4 in term.headers
        assert 0x7A0 in term.headers

    @pytest.mark.asyncio
    async def test_json_output_never_prompts_or_runs(self, tmp_path, monkeypatch):
        _mk_profile(tmp_path)
        term = FakeTerminal()
        monkeypatch.setattr(disc.sys, "stdin", _FakeStdin(tty=True, line="y\n"))
        await disc._offer_identify(term, ALIVE, identify=False, as_json=True)
        assert term.headers == []

    @pytest.mark.asyncio
    async def test_non_tty_does_not_prompt(self, tmp_path, monkeypatch):
        _mk_profile(tmp_path)
        term = FakeTerminal()
        monkeypatch.setattr(disc.sys, "stdin", _FakeStdin(tty=False))
        await disc._offer_identify(term, ALIVE, identify=False, as_json=False)
        assert term.headers == []

    @pytest.mark.asyncio
    async def test_tty_yes_runs(self, tmp_path, monkeypatch):
        _mk_profile(tmp_path)
        term = FakeTerminal()
        monkeypatch.setattr(disc.sys, "stdin", _FakeStdin(tty=True, line="y\n"))
        await disc._offer_identify(term, ALIVE, identify=False, as_json=False)
        assert term.headers  # ran

    @pytest.mark.asyncio
    async def test_tty_no_declines(self, tmp_path, monkeypatch):
        _mk_profile(tmp_path)
        term = FakeTerminal()
        monkeypatch.setattr(disc.sys, "stdin", _FakeStdin(tty=True, line="\n"))
        await disc._offer_identify(term, ALIVE, identify=False, as_json=False)
        assert term.headers == []

    @pytest.mark.asyncio
    async def test_empty_alive_is_noop(self, tmp_path):
        _mk_profile(tmp_path)
        term = FakeTerminal()
        await disc._offer_identify(term, [], identify=True, as_json=False)
        assert term.headers == []


class TestDiscoverCrossReference:
    @pytest.mark.asyncio
    async def test_results_report_known_vs_new(self, tmp_path, monkeypatch, capsys):
        _mk_profile(tmp_path, seed_ecus=SEED_BMS)

        class SweepTerminal(FakeTerminal):
            async def send_uds(self, cmd, timeout=None):
                # Only 0x7E4 responds during the sweep.
                if self.headers and self.headers[-1] == 0x7E4:
                    return {"ok": True, "bytes": b"\x50\x01", "hex": "5001", "raw": "5001"}
                return {"ok": False, "error": "NO DATA", "raw": "NO DATA"}

        term = SweepTerminal()
        # Non-tty stdin so the offer is skipped in this integration check.
        monkeypatch.setattr(disc.sys, "stdin", _FakeStdin(tty=False))
        await disc.mode_discover(term, (0x7E4, 0x7E5), verbose=False, as_json=False, delay=0.0)
        out = capsys.readouterr().out
        assert "Cross-reference:" in out
        assert "[BMS]" in out
        assert "1 known" in out
