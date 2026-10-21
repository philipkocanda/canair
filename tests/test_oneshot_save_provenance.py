"""Provenance of the one-shot ``--save`` producers (discover/scan/raw).

Two recorded-capture defects, both found in a real contribution from a car whose
profile uses ``rx_offset: 0x20``:

- the saved ``transport`` was re-resolved from configuration instead of read off
  the live client, so a ``--transport wican-ws`` run was recorded as the default
  ``slcan-tcp``;
- a discover responder's saved ``rx`` was recomputed as ``TX + 0x08`` — ignoring
  the profile's ``addressing:`` and disagreeing with the RX the sweep printed —
  and its probed TX was not recorded at all.

The fake terminal reports transport ``"fake"``, which no configuration resolves
to, so a saved ``"fake"`` proves the label came from the client.
"""

from __future__ import annotations

import json

import pytest

from canlib import profile
from canlib.modes import discover as disc
from canlib.pids import clear_cache
from tests._fakes import FakeTerminal, nrc, ok

PROFILE_YAML = 'car_model: "T"\ninit: "ATSP6;"\naddressing:\n  rx_offset: 0x20\n'
BRAKE = "BRAKE:\n  tx_id: 0x70E\n  rx_id: 0x70F\n  identity:\n    id_protocol: KWP2000\n"
ABS = "ABS:\n  tx_id: 0x740\n  identity:\n    id_protocol: UDS\n"


@pytest.fixture
def prof(tmp_path):
    saved = profile._active
    root = tmp_path / "prof"
    (root / "ecus").mkdir(parents=True)
    (root / "captures").mkdir()
    (root / "profile.yaml").write_text(PROFILE_YAML)
    (root / "ecus" / "brake.yaml").write_text(BRAKE)
    (root / "ecus" / "abs.yaml").write_text(ABS)
    profile.set_active(str(root))
    clear_cache()
    yield root
    profile._active = saved
    clear_cache()


def _saved_session(root):
    files = sorted((root / "captures").glob("*.json"))
    assert len(files) == 1, files
    sessions = json.loads(files[0].read_text())["sessions"]
    assert len(sessions) == 1
    return sessions[0]


class TestRxForTxHonoursTheProfile:
    def test_unregistered_tx_uses_the_profile_rx_offset(self, prof):
        from canlib.ecus import rx_addr_str, rx_for_tx

        assert rx_for_tx(0x74E) == 0x76E
        assert rx_addr_str(0x74E) == "0x76E"

    def test_registered_rx_id_override_wins(self, prof):
        from canlib.ecus import rx_for_tx

        assert rx_for_tx(0x70E) == 0x70F
        assert rx_for_tx(0x740) == 0x760

    def test_an_explicit_offset_still_wins(self, prof):
        from canlib.ecus import rx_for_tx

        assert rx_for_tx(0x74E, rx_offset=0x08) == 0x756


class TestDiscoverSave:
    @pytest.mark.asyncio
    async def test_records_tx_profile_rx_and_client_transport(self, prof, capsys):
        term = FakeTerminal(
            {
                (0x70E, "1001"): nrc(0x12, "subFunctionNotSupported"),
                (0x740, "1001"): ok("5001003201F4"),
                (0x74E, "1001"): ok("5001003201F4"),
            },
            key_by_header=True,
        )
        await disc.mode_discover(
            term,
            (0x70E, 0x74E),
            verbose=False,
            as_json=False,
            delay=0.0,
            save=True,
            label="sweep",
            vehicle_states=["READY"],
        )
        out = capsys.readouterr().out
        session = _saved_session(prof)

        assert session["transport"] == "fake"
        responding = session["captures"][0]["scan_results"]["responding"]
        got = {(r["tx"], r["rx"], r["response"]) for r in responding}
        assert got == {
            ("0x70E", "0x70F", "BRAKE (NRC 0x12)"),
            ("0x740", "0x760", "ABS (positive)"),
            ("0x74E", "0x76E", "positive"),  # unregistered: profile +0x20, not +8
        }
        # The saved RX is the one the sweep printed.
        assert "0x74E (RX 0x76E) [NEW]" in out


class TestScanAndRawSave:
    @pytest.mark.asyncio
    async def test_scan_records_the_client_transport(self, prof):
        from canlib.modes.scan import mode_scan

        term = FakeTerminal({"220100": ok("620100AA")})
        await mode_scan(term, 0x74E, 0x22, (0x0100, 0x0100), False, False, save=True, label="scan")
        session = _saved_session(prof)
        assert session["transport"] == "fake"
        assert session["captures"][0]["rx"] == "0x76E"

    @pytest.mark.asyncio
    async def test_raw_records_the_client_transport(self, prof):
        from canlib.modes.raw import mode_raw

        term = FakeTerminal({"2101": ok("6101AA")})
        await mode_raw(term, "74E:2101", False, False, save=True, label="raw")
        session = _saved_session(prof)
        assert session["transport"] == "fake"
        assert session["captures"][0]["rx"] == "0x76E"


class TestJournaledSaveNeverGuesses:
    def test_a_session_without_transport_is_saved_without_one(self, prof):
        """Re-resolving config would ignore --transport/--wican and fallback."""
        from canlib.captures import build_raw_session, save_session_journaled

        session = build_raw_session("0x76E", 0x74E, "2101", ok("6101AA"), "L", [], "")
        save_session_journaled(session, prof / "captures")
        assert "transport" not in _saved_session(prof)
