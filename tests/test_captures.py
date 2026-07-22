"""Tests for capture session builders and metadata resolution."""

from unittest.mock import patch

import yaml

from canlib.captures import (
    build_query_session,
    resolve_metadata,
    save_session,
)
from canlib.commands.captures import (
    _clean,
    _gather_query,
    _group_sessions,
    _is_hex_payload,
    cmd_diff,
    cmd_latest,
    cmd_list,
    cmd_sessions,
    cmd_summary,
)


class TestIsHexPayload:
    def test_valid_hex(self):
        assert _is_hex_payload("5001")
        assert _is_hex_payload("62BC0140000000000002")

    def test_spaces_tolerated(self):
        assert _is_hex_payload("50 01")

    def test_non_hex_rejected(self):
        # Legacy captures that stashed an outcome under `payload`.
        assert not _is_hex_payload("NO DATA")
        assert not _is_hex_payload("NO DATA x3")

    def test_empty_and_none_rejected(self):
        assert not _is_hex_payload("")
        assert not _is_hex_payload(None)

    def test_odd_length_rejected(self):
        assert not _is_hex_payload("500")


class TestGatherQueryFiltersNonHex:
    def test_non_hex_payloads_excluded(self):
        entries = [
            {"ecu": "IGPM", "pid": "1001", "payload": "NO DATA", "date": "2026-04-16"},
            {"ecu": "IGPM", "pid": "1001", "payload": "5001", "date": "2026-04-16"},
        ]
        matched, _ = _gather_query(entries, "IGPM:1001", warn=False)
        assert [e["payload"] for e in matched] == ["5001"]


class TestResolveMetadata:
    def test_label_given_is_noninteractive(self):
        """When a label is supplied, no prompt is shown and flags are used verbatim."""
        # input() would raise if called — proving non-interactive.
        with patch("builtins.input", side_effect=AssertionError("should not prompt")):
            meta = resolve_metadata("My label", "ready, parked", "some notes")
        assert meta == ("My label", "ready, parked", "some notes")

    def test_label_given_defaults_empty_state_notes(self):
        with patch("builtins.input", side_effect=AssertionError("should not prompt")):
            meta = resolve_metadata("Only label", None, None)
        assert meta == ("Only label", "", "")

    def test_no_label_falls_back_to_prompt(self):
        with patch("builtins.input", side_effect=["Prompted", "charging", "n"]):
            meta = resolve_metadata(None, None, None, suggested_label="sugg")
        assert meta == ("Prompted", "charging", "n")

    def test_no_label_prompt_cancelled(self):
        with patch("builtins.input", side_effect=KeyboardInterrupt):
            meta = resolve_metadata(None, None, None)
        assert meta is None


class TestBuildQuerySession:
    def test_groups_and_uppercases(self):
        # ecu_ref is the ECU CAN response address (RX = request TX + 8).
        results = [
            ("0x7EB", "2102", "6102aabb", ""),   # MCU (0x7E3 + 8)
            ("0x7EA", "2101", "6101ccdd", "12:00:01"),  # VCU (0x7E2 + 8)
        ]
        s = build_query_session(results, "lbl", "ready, parked", "notes here")
        assert s["label"] == "lbl"
        assert s["state"] == "ready, parked"
        assert s["notes"] == "notes here"
        assert "date" in s
        assert s["captures"][0] == {"ecu": "0x7EB", "pid": "2102", "payload": "6102AABB"}
        # time preserved when present
        assert s["captures"][1]["time"] == "12:00:01"
        assert s["captures"][1]["payload"] == "6101CCDD"

    def test_empty_state_notes_omitted(self):
        s = build_query_session([("0x7EC", "2101", "6101", "")], "l", "", "")  # BMS
        assert "state" not in s
        assert "notes" not in s

    def test_roundtrips_and_appends_via_save_session(self, tmp_path):
        results = [("0x7EB", "2102", "6102AABB", "")]  # MCU
        s = build_query_session(results, "Live ref", "ready, parked", "18C")
        save_session(s, tmp_path)
        files = list(tmp_path.glob("*.yaml"))
        assert len(files) == 1
        data = yaml.safe_load(files[0].read_text())
        assert data["sessions"][0]["label"] == "Live ref"
        assert data["sessions"][0]["captures"][0]["payload"] == "6102AABB"


def _entry(**kw):
    """A minimal flat capture entry as load_all_captures would produce."""
    base = {
        "file": "2026-07-22.yaml", "date": "2026-07-22", "session_label": "",
        "state": "", "session_notes": "", "ecu": "MCU", "ecu_addr": "0x7E3",
        "pid": "2102", "payload": "6102AA", "response": None, "scan_results": None,
        "notes": "", "time": "", "label": "", "_session_idx": 0, "_capture_idx": 0,
    }
    base.update(kw)
    return base


class TestClean:
    def test_strips_ansi_and_control(self):
        # A note that captured raw arrow-key escapes must not corrupt output.
        assert _clean("hit 100\x1b[D\x1b[Dkm/h") == "hit 100km/h"

    def test_collapses_whitespace_and_newlines(self):
        assert _clean("line one\n  line two\t x") == "line one line two x"

    def test_plain_passthrough(self):
        assert _clean("driving MT->KW") == "driving MT->KW"


class TestGroupSessions:
    def test_groups_by_file_and_session_idx(self):
        entries = [
            _entry(_session_idx=0, session_label="drive A", state="driving", time="16:00:00"),
            _entry(_session_idx=0, session_label="drive A", state="driving", time="16:00:05",
                   ecu="VCU"),
            _entry(_session_idx=1, session_label="park", state="ready", time="17:00:00"),
        ]
        sessions = _group_sessions(entries)
        assert len(sessions) == 2
        a = sessions[0]
        assert a["label"] == "drive A" and a["n"] == 2
        assert list(a["ecus"]) == ["MCU", "VCU"]
        assert a["times"] == ["16:00:00", "16:00:05"]

    def test_same_label_distinct_sessions_not_merged(self):
        # Two sessions sharing a label are still distinct by _session_idx.
        entries = [
            _entry(_session_idx=0, session_label="dup", time="10:00:00"),
            _entry(_session_idx=1, session_label="dup", time="11:00:00"),
        ]
        assert len(_group_sessions(entries)) == 2

    def test_chronological_order(self):
        entries = [
            _entry(_session_idx=0, date="2026-07-22", time="18:00:00"),
            _entry(_session_idx=1, date="2026-07-20", time="09:00:00"),
        ]
        sessions = _group_sessions(entries)
        assert [s["date"] for s in sessions] == ["2026-07-20", "2026-07-22"]

    def test_distinct_capture_notes_deduped(self):
        entries = [
            _entry(_session_idx=0, notes="note X"),
            _entry(_session_idx=0, notes="note X"),
            _entry(_session_idx=0, notes="note Y"),
        ]
        assert _group_sessions(entries)[0]["cap_notes"] == ["note X", "note Y"]


class TestCmdSessions:
    def test_text_output_shows_metadata(self, capsys):
        entries = [
            _entry(session_label="ESC drive", state="driving MT->KW",
                   session_notes="highway then city", time="16:51:52.4"),
        ]
        cmd_sessions(entries)
        out = capsys.readouterr().out
        assert "driving MT->KW" in out
        assert "ESC drive" in out
        assert "highway then city" in out
        assert "1 captures" in out and "MCU" in out

    def test_json_output(self, capsys):
        entries = [
            _entry(session_label="lbl", state="driving", session_notes="n",
                   time="16:00:00", ecu="MCU"),
            _entry(session_label="lbl", state="driving", time="16:00:09", ecu="VCU"),
        ]
        cmd_sessions(entries, as_json=True)
        import json
        data = json.loads(capsys.readouterr().out)
        assert len(data) == 1
        s = data[0]
        assert s["captures"] == 2
        assert s["ecus"] == ["MCU", "VCU"]
        assert s["time_start"] == "16:00:00" and s["time_end"] == "16:00:09"
        assert s["state"] == "driving" and s["notes"] == "n"

    def test_json_empty(self, capsys):
        cmd_sessions([], as_json=True)
        import json
        assert json.loads(capsys.readouterr().out) == []


class TestCmdSummaryJson:
    def test_json_shape(self, capsys):
        import json

        entries = [
            _entry(ecu="BMS", payload="6101AA"),
            _entry(ecu="BMS", payload="", scan_results={"responding": []}),
            _entry(ecu="VCU", payload="", response="NO DATA"),
        ]
        cmd_summary(entries, as_json=True)
        data = json.loads(capsys.readouterr().out)
        assert data["entries"] == 3
        assert data["payloads"] == 1 and data["scans"] == 1 and data["responses"] == 1
        assert data["by_ecu"] == {"BMS": 2, "VCU": 1}
        assert data["by_date"] == {"2026-07-22": 3}


class TestCmdListJson:
    def test_json_lists_matched_and_unmatched(self, capsys):
        import json

        entries = [
            _entry(ecu="BMS", pid="21F2", payload="61F2AABB", state="ready"),
            _entry(ecu="VCU", pid="2101", payload="6101CC"),
        ]
        cmd_list(entries, "BMS:21F2 IGPM:BC03", as_json=True)
        data = json.loads(capsys.readouterr().out)
        assert data["matched"] == 1
        assert data["captures"][0]["ecu"] == "BMS"
        assert data["captures"][0]["payload"] == "61F2AABB"
        assert data["captures"][0]["state"] == "ready"
        # IGPM:BC03 matched nothing → reported under unmatched.
        assert any("BC03" in u for u in data["unmatched"])


class TestCmdLatestJson:
    def test_json_latest_per_pid(self, capsys):
        import json

        entries = [
            _entry(ecu="BMS", pid="2101", payload="6101AA", date="2026-07-20"),
            _entry(ecu="BMS", pid="2101", payload="6101BB", date="2026-07-22"),
        ]
        cmd_latest(entries, None, as_json=True)
        data = json.loads(capsys.readouterr().out)
        assert len(data) == 1  # one ECU+PID, latest kept
        assert data[0]["payload"] == "6101BB"

    def test_json_empty(self, capsys):
        import json

        cmd_latest([], None, as_json=True)
        assert json.loads(capsys.readouterr().out) == []


class TestCmdDiffJson:
    def test_json_groups_unique_payloads(self, capsys):
        import json

        entries = [
            _entry(ecu="BMS", pid="21F2", payload="61F2AA"),
            _entry(ecu="BMS", pid="21F2", payload="61F2AA"),  # dup
            _entry(ecu="BMS", pid="21F2", payload="61F2BB"),
        ]
        cmd_diff(entries, "BMS:21F2", as_json=True)
        data = json.loads(capsys.readouterr().out)
        assert len(data) == 1
        g = data[0]
        assert g["ecu"] == "BMS" and g["pid"] == "21F2"
        assert g["total"] == 3 and g["unique"] == 2
        assert g["payloads"] == ["61F2AA", "61F2BB"]  # unique only by default

    def test_json_show_all_lists_every_payload(self, capsys):
        import json

        entries = [
            _entry(ecu="BMS", pid="21F2", payload="61F2AA"),
            _entry(ecu="BMS", pid="21F2", payload="61F2AA"),
        ]
        cmd_diff(entries, "BMS:21F2", show_all=True, as_json=True)
        data = json.loads(capsys.readouterr().out)
        assert data[0]["payloads"] == ["61F2AA", "61F2AA"]

    def test_json_empty(self, capsys):
        import json

        cmd_diff([], "BMS:21F2", as_json=True)
        assert json.loads(capsys.readouterr().out) == []
