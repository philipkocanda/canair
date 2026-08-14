"""Persisting confirmed response-frame counts back into the profile.

Covers the two ends the ledger connects: the raw (``slcan-tcp``) transport
observing a frame count at all, and ``run_session_guarded`` banking a session's
confirmed counts into ``ecus/`` on the way out.

Device-free throughout — the raw path is driven with a stub ISO-TP client and the
dispatch hook with a fake terminal.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from types import SimpleNamespace

import pytest

from canlib.frame_counts import FrameCountLedger, frames_for_payload
from canlib.modes.dispatch import run_session_guarded
from canlib.pids_edit import set_response_frames

from ._fakes import FakeTerminal

_ECU_YAML = """\
BMS:
  tx_id: 0x7E4
  pids:
    "2101":
      status: active
      parameters:
        SOC:
          expression: B09/2
    "2105":
      status: active
      parameters:
        SOH:
          expression: B27/10
"""


@pytest.fixture
def pids_dir(tmp_path: Path) -> Path:
    d = tmp_path / "ecus"
    d.mkdir()
    (d / "bms.yaml").write_text(_ECU_YAML)
    return d


@pytest.fixture
def pids_data(pids_dir: Path) -> dict:
    from canlib.pids import load_pids

    return load_pids(pids_dir)


def _reload(pids_dir: Path) -> dict:
    from canlib.pids import load_pids

    return load_pids(pids_dir)["ecus"]["BMS"]["pids"]


@pytest.fixture
def redirect_writes(pids_dir: Path, monkeypatch):
    """Point ``persist``'s editor at the temp profile instead of the active one.

    ``persist`` imports its collaborators lazily, so the patch has to land on the
    modules it resolves them from rather than on ``canlib.response_frames``.
    """
    import canlib.pids_edit as pids_edit
    import canlib.profile as profile

    def _set(ecu, pid, frames):
        return set_response_frames(ecu, pid, frames, pids_dir=pids_dir)

    monkeypatch.setattr(pids_edit, "set_response_frames", _set)
    monkeypatch.setattr(profile, "require_writable_definitions", lambda: None)
    return pids_dir


def _args(**kw):
    base = {"no_learn_frames": False, "verbose": False, "timings": False, "json": False}
    base.update(kw)
    return SimpleNamespace(**base)


async def _noop_dispatch(*a, **kw):
    return None


@pytest.fixture
def stub_dispatch(monkeypatch):
    """Reduce the session to just its teardown, which is what is under test."""
    import canlib.modes.dispatch as dispatch

    monkeypatch.setattr(dispatch, "dispatch_mode", _noop_dispatch)


class TestRawPathObservesFrameCounts:
    """The raw transport reassembles ISO-TP itself, so it must derive the count.

    ``parse_uds_response`` counts *response lines*, and the raw path hands it one
    already-reassembled message — which is why every raw read used to report a
    frame count of 1 regardless of length.
    """

    def test_a_multi_frame_payload_is_counted_from_its_length(self):
        # 23 bytes is the live BCM 22C00B read: four frames.
        assert frames_for_payload(23) == 4

    def test_a_confirmed_observation_needs_agreement(self):
        # The raw path has no digit to hold, so repetition is its only evidence.
        ledger = FrameCountLedger()
        for _ in range(3):
            ledger.observe((0x7E4, "2101"), 4)
        assert ledger.confirmed() == {(0x7E4, "2101"): 4}


class TestSessionWriteBack:
    """``run_session_guarded`` banks the session's confirmed counts on the way out."""

    def _run(self, args, terminal, pids_data) -> int:
        return asyncio.run(
            run_session_guarded(args, terminal, pids_data, "10.0.0.1", transport_label="TEST")
        )

    def test_a_confirmed_count_reaches_the_yaml(self, pids_data, redirect_writes, stub_dispatch):
        term = FakeTerminal()
        term.frame_counts.observe((0x7E4, "2101"), 4)
        term.frame_counts.confirm((0x7E4, "2101"), 4)

        assert self._run(_args(), term, pids_data) == 0
        assert _reload(redirect_writes)["2101"]["response_frames"] == 4

    def test_an_unconfirmed_count_is_not_written(self, pids_data, redirect_writes, stub_dispatch):
        # One observation is not evidence: an undercount must never be persisted.
        term = FakeTerminal()
        term.frame_counts.observe((0x7E4, "2101"), 4)

        self._run(_args(), term, pids_data)
        assert "response_frames" not in _reload(redirect_writes)["2101"]

    def test_a_retired_count_is_cleared_from_the_yaml(
        self, pids_dir, redirect_writes, stub_dispatch
    ):
        set_response_frames("BMS", "2101", 4, pids_dir=pids_dir)
        from canlib.pids import load_pids

        data = load_pids(pids_dir)

        term = FakeTerminal()
        term.frame_counts.observe((0x7E4, "2101"), 4)
        term.frame_counts.observe((0x7E4, "2101"), 6)

        self._run(_args(), term, data)
        assert "response_frames" not in _reload(pids_dir)["2101"]

    def test_no_learn_frames_writes_nothing(self, pids_data, redirect_writes, stub_dispatch):
        term = FakeTerminal()
        term.frame_counts.observe((0x7E4, "2101"), 4)
        term.frame_counts.confirm((0x7E4, "2101"), 4)

        self._run(_args(no_learn_frames=True), term, pids_data)
        assert "response_frames" not in _reload(redirect_writes)["2101"]

    def test_counts_are_banked_even_when_the_session_failed(
        self, pids_data, redirect_writes, monkeypatch
    ):
        # The evidence was already collected; a dropped bus must not discard it.
        import canlib.modes.dispatch as dispatch

        async def _boom(*a, **kw):
            raise ConnectionError("bus went away")

        monkeypatch.setattr(dispatch, "dispatch_mode", _boom)

        term = FakeTerminal()
        term.frame_counts.observe((0x7E4, "2105"), 2)
        term.frame_counts.confirm((0x7E4, "2105"), 2)

        assert self._run(_args(), term, pids_data) == 1
        assert _reload(redirect_writes)["2105"]["response_frames"] == 2

    def test_a_write_failure_never_fails_the_session(self, pids_data, monkeypatch, stub_dispatch):
        # Persisting is bookkeeping on the way out; it must not turn a good read
        # into a non-zero exit.
        import canlib.pids_edit as pids_edit
        import canlib.profile as profile

        monkeypatch.setattr(profile, "require_writable_definitions", lambda: None)
        monkeypatch.setattr(
            pids_edit,
            "set_response_frames",
            lambda *a, **kw: (_ for _ in ()).throw(OSError("nope")),
        )

        term = FakeTerminal()
        term.frame_counts.observe((0x7E4, "2101"), 4)
        term.frame_counts.confirm((0x7E4, "2101"), 4)

        assert self._run(_args(), term, pids_data) == 0

    def test_a_steady_state_session_rewrites_nothing(
        self, pids_dir, redirect_writes, stub_dispatch
    ):
        set_response_frames("BMS", "2101", 4, pids_dir=pids_dir)
        from canlib.pids import load_pids

        data = load_pids(pids_dir)
        before = (pids_dir / "bms.yaml").read_text()

        term = FakeTerminal()
        term.frame_counts.observe((0x7E4, "2101"), 4)
        term.frame_counts.confirm((0x7E4, "2101"), 4)

        self._run(_args(), term, data)
        assert (pids_dir / "bms.yaml").read_text() == before

    def test_an_empty_ledger_touches_nothing(self, pids_data, redirect_writes, stub_dispatch):
        before = (redirect_writes / "bms.yaml").read_text()
        self._run(_args(), FakeTerminal(), pids_data)
        assert (redirect_writes / "bms.yaml").read_text() == before
