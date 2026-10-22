"""Response addresses beyond ``TX + 0x08``: the profile default, 29-bit captures, discover.

``ecus._profile_addressing`` is the fallback rule for an address the registry does not know;
the capture schema must accept the 29-bit RX that raw/scan/import/discover now record on a
29-bit profile; and a discover sweep's ``--json`` must report the RX its saved capture holds.
"""

from __future__ import annotations

import json

import pytest

from canlib import profile
from canlib.addressing import DEFAULT_MODE, DEFAULT_RX_OFFSET, AddressingMode
from canlib.pids import clear_cache
from tests._fakes import FakeTerminal, ok

FIXED_29 = (
    'car_model: "T"\ninit: "ATSP7;"\naddressing:\n  mode: normal_fixed_29bit\n  rx_offset: 0x08\n'
)


@pytest.fixture
def make_profile(tmp_path):
    saved = profile._active

    def make(meta: str, *, ecus: dict[str, str] | None = None, with_ecus_dir: bool = True):
        root = tmp_path / "prof"
        root.mkdir()
        (root / "captures").mkdir()
        (root / "profile.yaml").write_text(meta)
        if with_ecus_dir:
            (root / "ecus").mkdir()
            for name, body in (ecus or {}).items():
                (root / "ecus" / f"{name}.yaml").write_text(body)
        profile.set_active(str(root))
        clear_cache()
        return root

    yield make
    profile._active = saved
    clear_cache()


class TestProfileAddressing:
    def test_no_profile_falls_back_to_the_defaults(self, monkeypatch):
        from canlib import ecus
        from canlib.profile import ProfileError

        def boom():
            raise ProfileError("no profile")

        monkeypatch.setattr("canlib.pids.load_pids", lambda *a, **k: boom())
        assert ecus._profile_addressing() == (DEFAULT_RX_OFFSET, DEFAULT_MODE)

    def test_a_fixed_29bit_profile_swaps_an_unregistered_id(self, make_profile):
        from canlib import ecus

        make_profile(FIXED_29)
        assert ecus._profile_addressing() == (0x08, AddressingMode.NORMAL_FIXED_29BIT)
        assert ecus.rx_for_tx(0x18DA10F1, {}) == 0x18DAF110

    def test_a_profile_without_ecus_dir_still_answers(self, make_profile):
        """A freshly scaffolded bundle has no ecus/ yet; that is not an error here."""
        from canlib import ecus

        make_profile(
            'car_model: "T"\ninit: "x"\naddressing:\n  rx_offset: 0x20\n', with_ecus_dir=False
        )
        assert ecus._profile_addressing() == (0x20, DEFAULT_MODE)


class TestCaptureSchemaAccepts29BitRx:
    def _validate(self, path, rx_addrs):
        from jsonschema import Draft202012Validator

        from canlib.commands.validate.captures import CAPTURES_SCHEMA_FILE, validate_captures_file

        schema = json.loads(CAPTURES_SCHEMA_FILE.read_text())
        return validate_captures_file(path, Draft202012Validator(schema), rx_addrs)

    @pytest.mark.parametrize("rx", ["0x18DAF110", "0x7EC", "broadcast"])
    def test_valid_addresses(self, tmp_path, rx):
        path = tmp_path / "2026-10-22.json"
        cap = {"rx": rx, "pid": "22F190", "payload": "62F190AA", "time": "10:00:00"}
        path.write_text(
            json.dumps({"sessions": [{"date": "2026-10-22", "label": "l", "captures": [cap]}]})
        )
        assert self._validate(path, {0x18DAF110, 0x7EC}) == []

    @pytest.mark.parametrize("rx", ["0x7E", "0x118DAF110", "7EC"])
    def test_malformed_addresses_are_still_rejected(self, tmp_path, rx):
        path = tmp_path / "2026-10-22.json"
        cap = {"rx": rx, "pid": "22F190", "payload": "62F190AA", "time": "10:00:00"}
        path.write_text(
            json.dumps({"sessions": [{"date": "2026-10-22", "label": "l", "captures": [cap]}]})
        )
        assert any("does not match" in e for e in self._validate(path, set()))


def _saved(root) -> dict:
    [path] = sorted((root / "captures").glob("*.json"))
    [session] = json.loads(path.read_text())["sessions"]
    return session


class TestDiscoverRxConsistency:
    @pytest.mark.asyncio
    async def test_json_rx_matches_the_saved_capture(self, make_profile, capsys):
        from canlib.modes import discover as disc

        root = make_profile(
            'car_model: "T"\ninit: "ATSP6;"\naddressing:\n  rx_offset: 0x20\n',
            ecus={"abs": "ABS:\n  tx_id: 0x740\n  rx_id: 0x761\n"},
        )
        term = FakeTerminal(
            {(0x740, "1001"): ok("5001"), (0x74E, "1001"): ok("5001")}, key_by_header=True
        )
        await disc.mode_discover(
            term, (0x740, 0x74E), verbose=False, as_json=True, delay=0.0, save=True, label="s"
        )
        out = capsys.readouterr().out
        doc = json.loads(out[out.index("{") : out.rindex("}") + 1])
        printed = {(a["tx"], a["rx"]) for a in doc["alive"]}
        responding = _saved(root)["captures"][0]["scan_results"]["responding"]
        saved = {(r["tx"], r["rx"]) for r in responding}
        assert printed == saved == {("0x740", "0x761"), ("0x74E", "0x76E")}

    @pytest.mark.asyncio
    async def test_a_29bit_sweep_saves_valid_29bit_addresses(self, make_profile, capsys):
        from jsonschema import Draft202012Validator

        from canlib.commands.validate.captures import CAPTURES_SCHEMA_FILE, validate_captures_file
        from canlib.modes import discover as disc

        root = make_profile(FIXED_29)
        term = FakeTerminal({(0x18DA10F1, "1001"): ok("5001")}, key_by_header=True)
        await disc.mode_discover(
            term,
            (0x10, 0x11),
            verbose=False,
            as_json=False,
            delay=0.0,
            save=True,
            label="s",
            mode=AddressingMode.NORMAL_FIXED_29BIT,
        )
        session = _saved(root)
        [entry] = session["captures"][0]["scan_results"]["responding"]
        assert (entry["tx"], entry["rx"]) == ("0x18DA10F1", "0x18DAF110")
        assert "0x18DA10F1 (RX 0x18DAF110)" in capsys.readouterr().out
        [path] = sorted((root / "captures").glob("*.json"))
        validator = Draft202012Validator(json.loads(CAPTURES_SCHEMA_FILE.read_text()))
        assert validate_captures_file(path, validator, set()) == []
