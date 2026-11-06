"""The physical ``discover`` sweep never probes the functional id.

``0x7DF`` lies inside the default 11-bit range 0x700-0x7EF. A ``10 01`` sent there would reach
every ECU at once, so the safety guard refused it and the sweep reported it as an error. The
sweep now leaves it out, says so, and refuses a range that holds nothing else before connecting.
"""

from __future__ import annotations

import argparse

import pytest

from canair.cli import profile_context
from canair.cli.commands import discover as discover_cmd
from canair.cli.profile_context import active
from canair.ecus.pids import clear_cache
from canair.live.discover import physical as disc
from canair.uds.addressing import FUNCTIONAL_11BIT_ID, AddressingMode
from tests.support.fakes import FakeTerminal

NO_DATA = {"ok": False, "error": "NO DATA", "raw": "NO DATA"}


@pytest.fixture
def blank_profile(tmp_path, monkeypatch):
    saved = profile_context._active
    root = tmp_path / "prof"
    (root / "ecus").mkdir(parents=True)
    (root / "captures").mkdir()
    (root / "profile.yaml").write_text('car_model: "T"\ninit: "ATSP6;"\n')
    profile_context.set_active(str(root))
    clear_cache()
    monkeypatch.setattr("sys.stdin.isatty", lambda: False)
    yield root
    profile_context._active = saved
    clear_cache()


class TestTargets:
    def test_default_range_skips_the_functional_id(self):
        ids = disc.discovery_targets((0x700, 0x7EF))
        assert FUNCTIONAL_11BIT_ID not in ids
        assert len(ids) == 0x7EF - 0x700  # 240 addresses less one
        assert 0x7DE in ids and 0x7E0 in ids

    def test_29bit_target_bytes_are_untouched(self):
        ids = disc.discovery_targets((0x00, 0xFF), AddressingMode.NORMAL_FIXED_29BIT)
        assert len(ids) == 256

    def test_a_range_of_only_the_functional_id_is_empty(self):
        assert disc.discovery_targets((0x7DF, 0x7DF)) == []


class TestSweep:
    @pytest.mark.asyncio
    async def test_never_addresses_the_functional_id(self, blank_profile, capsys):
        term = FakeTerminal(default=NO_DATA)
        await disc.mode_discover(
            term, (0x7DD, 0x7E1), verbose=False, as_json=False, delay=0.0, profile=active()
        )
        out = capsys.readouterr().out
        assert term.headers == [0x7DD, 0x7DE, 0x7E0, 0x7E1]
        assert "0x7DF, the functional id" in out
        assert "Errors" not in out

    @pytest.mark.asyncio
    async def test_no_skip_line_when_the_range_misses_it(self, blank_profile, capsys):
        term = FakeTerminal(default=NO_DATA)
        await disc.mode_discover(
            term, (0x7E0, 0x7E1), verbose=False, as_json=False, delay=0.0, profile=active()
        )
        assert "functional id" not in capsys.readouterr().out

    @pytest.mark.asyncio
    async def test_an_empty_sweep_is_refused(self, blank_profile):
        with pytest.raises(ValueError, match="only the functional id 0x7DF"):
            await disc.mode_discover(
                FakeTerminal(default=NO_DATA),
                (0x7DF, 0x7DF),
                False,
                False,
                delay=0.0,
                profile=active(),
            )


def _args(**kw) -> argparse.Namespace:
    base = {
        "functional": False,
        "request": None,
        "window": None,
        "repeat": 1,
        "interval": 1.0,
        "range": None,
        "unsafe": False,
    }
    base.update(kw)
    return argparse.Namespace(**base)


class TestCommand:
    def test_only_the_functional_id_is_refused_before_connecting(
        self, blank_profile, monkeypatch, capsys
    ):
        def no_connection(*_a, **_kw):
            raise AssertionError("connected for a sweep with nothing to probe")

        monkeypatch.setattr(discover_cmd, "run_live", no_connection)
        assert discover_cmd.run(_args(range="7DF-7DF")) == 2
        err = capsys.readouterr().err
        assert "the functional id" in err and "--functional" in err

    def test_an_invalid_range_is_refused_before_connecting(self, blank_profile, capsys):
        assert "Invalid range" in discover_cmd._range_error(_args(range="7EF-700x"))

    def test_a_range_around_it_passes(self, blank_profile):
        assert discover_cmd._range_error(_args(range="7D0-7EF")) is None
        assert discover_cmd._range_error(_args()) is None
