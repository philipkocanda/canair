"""Sensitive PIDs and signals: masked on screen, never saved, never shared (canlib.privacy.sensitive)."""

from __future__ import annotations

import json

import pytest

from canlib import profile
from canlib.captures import io as capture_io
from canlib.ecus.pids import build_ecu_index, clear_cache, load_pids
from canlib.privacy import sensitive

META = 'car_model: "T"\ninit: "ATSP6;"\n'

# A synthetic ECU: a whole-PID flag (22F102), a signal-level flag (22F103), a plain PID.
CCM = """\
CCM:
  tx_id: 0x733
  pids:
    22F102:
      sensitive: true
      status: static
    22F103:
      status: static
      parameters:
        ID_HI:
          expression: B04
          sensitive: true
        VERSION:
          expression: B05
    22F101:
      status: static
      parameters:
        APP_SW:
          expression: B04
"""


@pytest.fixture(autouse=True)
def _reset():
    sensitive.set_reveal(False)
    sensitive.take_withheld_note()
    yield
    sensitive.set_reveal(False)
    sensitive.take_withheld_note()


@pytest.fixture
def prof(tmp_path):
    saved = profile._active
    root = tmp_path / "prof"
    (root / "captures").mkdir(parents=True)
    (root / "ecus").mkdir()
    (root / "profile.yaml").write_text(META)
    (root / "ecus" / "ccm.yaml").write_text(CCM)
    profile.set_active(str(root))
    clear_cache()
    yield root
    profile._active = saved
    clear_cache()


def _cap(pid: str, payload: str, rx: str = "0x73B") -> dict:
    return {"rx": rx, "pid": pid, "payload": payload, "time": "10:00:00.000"}


class TestFlags:
    def test_a_pid_is_sensitive_by_its_own_flag_or_any_signal(self):
        assert sensitive.pid_sensitive({"sensitive": True})
        assert sensitive.pid_sensitive({"parameters": {"A": {"sensitive": True}, "B": {}}})
        assert not sensitive.pid_sensitive({"parameters": {"A": {}}})
        assert not sensitive.pid_sensitive({"sensitive": "yes"})  # only a real boolean

    def test_a_signal_is_sensitive_by_its_own_flag_or_its_pids(self):
        assert sensitive.param_sensitive({"sensitive": True}, {})
        assert sensitive.param_sensitive({}, {"sensitive": True})
        assert not sensitive.param_sensitive({}, {})

    def test_the_set_is_keyed_by_name_request_and_response(self, prof):
        sset = sensitive.profile_set()
        assert sset.has("ccm", "22f102") and sset.has("CCM", "22F103")
        assert not sset.has("CCM", "22F101")
        assert sset.has_request(0x733, "22 F1 02")
        assert sset.has_capture(_cap("22F102", "62F102AABBCCDDEEFF"))
        assert not sset.has_capture(_cap("22F102", "62F102AABBCCDDEEFF", rx="0x7EC"))


class TestLiveMasking:
    def _entry(self, pid: str, payload: str, tx: int | str = 0x733):
        from canlib.modes.multi_batch import _decode_pid_result

        info = build_ecu_index(load_pids())["CCM"]["pids"][pid]
        return _decode_pid_result(
            pid, info, False, payload, bytes.fromhex(payload), None, target=tx
        )

    def test_a_sensitive_pid_hides_its_payload(self, prof):
        entry = self._entry("22F102", "62F102AABBCCDDEEFF")
        assert entry["raw_hex"] == sensitive.MASK and entry["sensitive"]

    def test_a_sensitive_signal_hides_its_value_and_the_raw_bytes_only(self, prof):
        entry = self._entry("22F103", "62F1033132", tx="CCM")
        values = {row[0]: row[1] for row in entry["params"]}
        assert values == {"ID_HI": sensitive.MASK, "VERSION": 0x32}
        assert entry["raw_hex"] == sensitive.MASK

    def test_a_plain_pid_is_untouched(self, prof):
        entry = self._entry("22F101", "62F10131313034")
        assert entry["raw_hex"] == "62F10131313034" and "sensitive" not in entry

    def test_reveal_shows_the_value_but_keeps_the_tag(self, prof):
        sensitive.set_reveal(True)
        entry = self._entry("22F102", "62F102AABBCCDDEEFF")
        assert entry["raw_hex"] == "62F102AABBCCDDEEFF" and entry["sensitive"]

    def test_the_masked_hex_line_says_how_to_reveal(self):
        from canlib.formatting import _render_hex_line

        line = _render_hex_line(sensitive.MASK, [], True).plain
        assert sensitive.MASK in line and "--reveal" in line

    def test_read_refuses_reveal_with_save(self, prof, capsys):
        from canlib.cli import main

        assert main(["read", "CCM:22F102", "--reveal", "--save"]) == 2
        assert "--reveal cannot be combined with --save" in capsys.readouterr().err


class TestNeverSaved:
    def test_the_journal_never_holds_a_sensitive_reply(self, prof, capsys):
        from canlib.captures.journal import CaptureJournal

        journal = CaptureJournal.open(prof / "captures", label="t")
        sensitive.set_reveal(True)  # reveal is display-only: still never written
        journal.append("0x73B", "22F102", "62F102AABBCCDDEEFF", "10:00:00.000")
        journal.append("0x73B", "22F101", "62F10131313034", "10:00:00.100")
        journal.append_response("0x73B", "22F103", "NRC 0x31")
        journal.flush()
        text = journal.path.read_text()
        assert "F102" not in text and "22F103" not in text and "22F101" in text
        out = journal.reconcile()
        assert out is not None
        assert "AABBCC" not in out.read_text()
        # The note rides on the save banner.
        assert "Not saved: CCM:22F102" in capsys.readouterr().out

    def test_save_session_drops_sensitive_rows_whoever_built_them(self, prof, capsys):
        from canlib.captures import save_session

        session = {
            "date": "2026-11-02",
            "label": "t",
            "captures": [_cap("22F102", "62F102AABBCCDDEEFF"), _cap("22F101", "62F10131313034")],
        }
        path = save_session(session, prof / "captures")
        assert "AABBCC" not in path.read_text()
        assert "Not saved: CCM:22F102" in capsys.readouterr().out


def _write_store(prof, captures: list[dict]) -> None:
    capture_io.dump_capture_file(
        prof / "captures" / "2026-11-02.json",
        {"sessions": [{"date": "2026-11-02", "label": "old", "captures": captures}]},
    )


class TestStoredHistory:
    def test_analyses_never_see_a_sensitive_reply(self, prof, capsys):
        from canlib.captures.store import _noted_sensitive, load_all_captures

        _noted_sensitive.clear()
        _write_store(prof, [_cap("22F102", "62F102AABBCCDDEEFF"), _cap("22F101", "62F101")])
        assert [e["pid"] for e in load_all_captures()] == ["22F101"]
        assert "purge-sensitive" in capsys.readouterr().err

    def test_validate_reports_and_purge_removes_it(self, prof):
        from canlib.captures.sensitive_purge import purge_dir, sensitive_findings

        _write_store(prof, [_cap("22F102", "62F102AABBCCDDEEFF"), _cap("22F101", "62F101")])
        path = prof / "captures" / "2026-11-02.json"
        sset = sensitive.profile_set()
        assert len(sensitive_findings(path, sset)) == 1
        assert [r.removed for r in purge_dir(prof / "captures", sset, dry_run=True)] == [1]
        assert "AABBCC" in path.read_text()  # dry run
        purge_dir(prof / "captures", sset)
        assert "AABBCC" not in path.read_text() and not sensitive_findings(path, sset)

    def test_a_session_of_only_sensitive_replies_is_dropped(self, prof):
        from canlib.captures.sensitive_purge import purge_dir

        _write_store(prof, [_cap("22F102", "62F102AABBCCDDEEFF")])
        (result,) = purge_dir(prof / "captures", sensitive.profile_set())
        assert result.sessions_dropped == 1
        doc = capture_io.load_capture_file(prof / "captures" / "2026-11-02.json")
        assert doc["sessions"] == []

    def test_purge_command_json_needs_yes(self, prof, capsys):
        from canlib.cli import main

        _write_store(prof, [_cap("22F102", "62F102AABBCCDDEEFF")])
        assert main(["captures", "purge-sensitive", "--json"]) == 1
        capsys.readouterr()
        assert main(["captures", "purge-sensitive", "--json", "--yes"]) == 0
        assert json.loads(capsys.readouterr().out)["removed"] == 1

    def test_a_hand_redacted_value_is_kept_everywhere(self, prof):
        from canlib.captures.sensitive_purge import purge_dir, sensitive_findings
        from canlib.captures.store import load_all_captures

        (mask,) = sensitive.REDACTED_VALUES
        redacted = {"rx": "0x73B", "pid": "22F102", "response": mask, "notes": "redacted"}
        _write_store(prof, [redacted, _cap("22F102", "62F102AABBCCDDEEFF")])
        path = prof / "captures" / "2026-11-02.json"
        sset = sensitive.profile_set()
        assert len(sensitive_findings(path, sset)) == 1  # the real reply only
        assert [e.get("response") for e in load_all_captures()] == [mask]
        purge_dir(prof / "captures", sset)
        assert mask in path.read_text() and not sensitive_findings(path, sset)

    def test_a_real_payload_beside_a_redacted_text_is_still_sensitive(self):
        (mask,) = sensitive.REDACTED_VALUES
        row = {"pid": "22F102", "payload": "62F102AABB", "response": mask}
        assert not sensitive.is_redacted(row)


class TestNeverShared:
    def test_contribute_strips_the_staged_copy(self, prof, tmp_path):
        from canlib.contribute import copy_profile

        _write_store(prof, [_cap("22F102", "62F102AABBCCDDEEFF"), _cap("22F101", "62F101")])
        warnings: list[str] = []
        dest = copy_profile(
            profile.active(), tmp_path / "ws", include_captures=True, warn=warnings.append
        )
        assert "AABBCC" not in (dest / "captures" / "2026-11-02.json").read_text()
        assert any("sensitive" in w for w in warnings)

    def test_autopid_never_ships_a_sensitive_signal(self, prof):
        from canlib.interop.autopid.profile import ship_profile

        data = load_pids()
        data["ecus"]["CCM"]["pids"]["22F103"]["status"] = "active"
        shipped = ship_profile(data)
        names = {p for sp in shipped.pids for p in sp.params}
        assert "ID_HI" not in names and "VERSION" in names

    def test_the_explorer_plots_no_sensitive_pid(self):
        from canlib.interop.explorer.series import _parameters

        ecu = {"pids": {"22F103": {"parameters": {"A": {"expression": "B04", "sensitive": True}}}}}
        assert _parameters(ecu, "22F103") is None


class TestEditor:
    def test_set_sensitive_on_a_pid_and_a_signal_round_trips(self, prof):
        from canlib.yaml_edit.pids import set_sensitive

        set_sensitive("CCM", "22F101", True, pids_dir=prof / "ecus")
        set_sensitive("CCM", "22F101", True, param="APP_SW", pids_dir=prof / "ecus")
        pid = load_pids(prof / "ecus")["ecus"]["CCM"]["pids"]["22F101"]
        assert pid["sensitive"] is True and pid["parameters"]["APP_SW"]["sensitive"] is True
        set_sensitive("CCM", "22F101", False, pids_dir=prof / "ecus")
        set_sensitive("CCM", "22F101", False, param="APP_SW", pids_dir=prof / "ecus")
        assert "sensitive" not in (prof / "ecus" / "ccm.yaml").read_text().split("22F101")[1]

    def test_validate_rejects_send_to_on_a_sensitive_signal(self, prof, capsys):
        text = (
            (prof / "ecus" / "ccm.yaml")
            .read_text()
            .replace(
                "          sensitive: true\n",
                "          sensitive: true\n          send_to: car/x\n",
            )
        )
        (prof / "ecus" / "ccm.yaml").write_text(text)
        clear_cache()
        from canlib.cli import main

        assert main(["validate", "pids"]) != 0
        assert "cannot declare send_to" in capsys.readouterr().out


class TestBundledIoniqIdentity:
    """KWP2000 1A80 and 1A90 on the VCU both carry the VIN, so both are masked."""

    VIN = "TESTVIN0000000000"

    @pytest.fixture
    def ioniq(self):
        saved = profile._active
        profile.set_active("hyundai-ioniq-ae-ev")
        clear_cache()
        yield
        profile._active = saved
        clear_cache()

    @pytest.mark.parametrize("request_hex", ["1A80", "1A90"])
    def test_the_vin_records_are_hidden(self, ioniq, request_hex):
        assert sensitive.request_hidden(0x7E2, request_hex)

    def test_the_part_number_records_are_not(self, ioniq):
        assert not sensitive.request_hidden(0x7E2, "1A8C")
        assert not sensitive.request_hidden(0x7E2, "1A91")

    @pytest.mark.asyncio
    async def test_identity_does_not_print_the_vin(self, ioniq, monkeypatch, capsys):
        from canlib.modes import identity as ident
        from canlib.modes import identity_decode as idec
        from tests.support.fakes import FakeTerminal
        from tests.support.fakes import ok as _ok

        monkeypatch.setattr(idec, "ecu_protocol", lambda tx: "kwp")
        vin_hex = self.VIN.encode().hex().upper()
        term = FakeTerminal({"1A80": _ok("5A80" + vin_hex), "1A90": _ok("5A90" + vin_hex)})
        await ident.mode_identity(term, 0x7E2, session=False, wake=False, as_json=False)
        out = capsys.readouterr().out
        assert self.VIN not in out and vin_hex not in out
        assert out.count(sensitive.MASK) >= 2
