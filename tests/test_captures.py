"""Tests for capture session builders and metadata resolution."""

from unittest.mock import patch

import pytest
import yaml

from canlib.captures import (
    build_query_session,
    resolve_metadata,
    save_session,
)
from canlib.commands._captures_query import (
    _build_pair_frames,
    _gather_query,
    _is_hex_payload,
    _pair_by_time,
)
from canlib.commands._captures_step import _render_step_pair_frame, cmd_step_pair
from canlib.commands.captures import (
    _clean,
    _group_sessions,
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
        assert meta == ("My label", ["ready", "parked"], "some notes")

    def test_label_given_defaults_empty_state_notes(self):
        with patch("builtins.input", side_effect=AssertionError("should not prompt")):
            meta = resolve_metadata("Only label", None, None)
        assert meta == ("Only label", [], "")

    def test_no_label_falls_back_to_prompt(self):
        with patch("builtins.input", side_effect=["Prompted", "charging", "n"]):
            meta = resolve_metadata(None, None, None, suggested_label="sugg")
        assert meta == ("Prompted", ["charging"], "n")

    def test_no_label_prompt_cancelled(self):
        with patch("builtins.input", side_effect=KeyboardInterrupt):
            meta = resolve_metadata(None, None, None)
        assert meta is None


class TestBuildQuerySession:
    def test_groups_and_uppercases(self):
        # ecu_ref is the ECU CAN response address (RX = request TX + 8).
        results = [
            ("0x7EB", "2102", "6102aabb", ""),  # MCU (0x7E3 + 8)
            ("0x7EA", "2101", "6101ccdd", "12:00:01"),  # VCU (0x7E2 + 8)
        ]
        s = build_query_session(results, "lbl", ["ready", "parked"], "notes here")
        assert s["label"] == "lbl"
        assert s["vehicle_states"] == ["ready", "parked"]
        assert s["notes"] == "notes here"
        assert "date" in s
        # A payload capture always gets a timestamp (Tranche 2.6): explicit
        # time preserved, missing time backfilled with the current HH:MM:SS.
        cap0 = s["captures"][0]
        assert cap0["ecu"] == "0x7EB" and cap0["pid"] == "2102" and cap0["payload"] == "6102AABB"
        assert cap0.get("time")  # backfilled, non-empty
        # time preserved when present
        assert s["captures"][1]["time"] == "12:00:01"
        assert s["captures"][1]["payload"] == "6101CCDD"

    def test_empty_state_notes_omitted(self):
        s = build_query_session([("0x7EC", "2101", "6101", "")], "l", [], "")  # BMS
        assert "vehicle_states" not in s
        assert "notes" not in s

    def test_keep_mode_unique_persisted(self):
        s = build_query_session([("0x7EC", "2101", "6101", "")], "l", [], "", keep_mode="unique")
        assert s["keep_mode"] == "unique"

    def test_keep_mode_all_not_persisted(self):
        # Only "unique" changes interpretation; don't clutter with keep-all/last.
        for mode in ("all", "last", None):
            s = build_query_session([("0x7EC", "2101", "6101", "")], "l", [], "", keep_mode=mode)
            assert "keep_mode" not in s

    def test_roundtrips_and_appends_via_save_session(self, tmp_path):
        results = [("0x7EB", "2102", "6102AABB", "")]  # MCU
        s = build_query_session(results, "Live ref", ["ready", "parked"], "18C")
        save_session(s, tmp_path)
        files = list(tmp_path.glob("*.yaml"))
        assert len(files) == 1
        data = yaml.safe_load(files[0].read_text())
        assert data["sessions"][0]["label"] == "Live ref"
        assert data["sessions"][0]["captures"][0]["payload"] == "6102AABB"


def _entry(**kw):
    """A minimal flat capture entry as load_all_captures would produce."""
    base = {
        "file": "2026-07-22.yaml",
        "date": "2026-07-22",
        "session_label": "",
        "vehicle_states": [],
        "session_notes": "",
        "ecu": "MCU",
        "ecu_addr": "0x7E3",
        "pid": "2102",
        "payload": "6102AA",
        "response": None,
        "scan_results": None,
        "notes": "",
        "time": "",
        "label": "",
        "_session_idx": 0,
        "_capture_idx": 0,
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
            _entry(
                _session_idx=0, session_label="drive A", vehicle_states=["driving"], time="16:00:00"
            ),
            _entry(
                _session_idx=0,
                session_label="drive A",
                vehicle_states=["driving"],
                time="16:00:05",
                ecu="VCU",
            ),
            _entry(_session_idx=1, session_label="park", vehicle_states=["ready"], time="17:00:00"),
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
            _entry(
                session_label="ESC drive",
                vehicle_states=["driving"],
                session_notes="highway then city",
                time="16:51:52.4",
            ),
        ]
        cmd_sessions(entries)
        out = capsys.readouterr().out
        assert "driving" in out
        assert "ESC drive" in out
        assert "highway then city" in out
        assert "1 captures" in out and "MCU" in out

    def test_json_output(self, capsys):
        entries = [
            _entry(
                session_label="lbl",
                vehicle_states=["driving"],
                session_notes="n",
                time="16:00:00",
                ecu="MCU",
            ),
            _entry(session_label="lbl", vehicle_states=["driving"], time="16:00:09", ecu="VCU"),
        ]
        cmd_sessions(entries, as_json=True)
        import json

        data = json.loads(capsys.readouterr().out)
        assert len(data) == 1
        s = data[0]
        assert s["captures"] == 2
        assert s["ecus"] == ["MCU", "VCU"]
        assert s["time_start"] == "16:00:00" and s["time_end"] == "16:00:09"
        assert s["vehicle_states"] == ["driving"] and s["notes"] == "n"

    def test_json_empty(self, capsys):
        cmd_sessions([], as_json=True)
        import json

        assert json.loads(capsys.readouterr().out) == []

    def test_keep_mode_shown_in_text(self, capsys):
        entries = [_entry(session_label="unlock events", keep_mode="unique", time="09:36:00")]
        cmd_sessions(entries)
        out = capsys.readouterr().out
        assert "keep:unique" in out

    def test_keep_mode_absent_when_not_unique(self, capsys):
        entries = [_entry(session_label="drive", time="16:00:00")]
        cmd_sessions(entries)
        assert "keep:" not in capsys.readouterr().out

    def test_keep_mode_in_json(self, capsys):
        entries = [_entry(session_label="lbl", keep_mode="unique", time="16:00:00")]
        cmd_sessions(entries, as_json=True)
        import json

        assert json.loads(capsys.readouterr().out)[0]["keep_mode"] == "unique"


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
            _entry(ecu="BMS", pid="21F2", payload="61F2AABB", vehicle_states=["ready"]),
            _entry(ecu="VCU", pid="2101", payload="6101CC"),
        ]
        cmd_list(entries, "BMS:21F2 IGPM:BC03", as_json=True)
        data = json.loads(capsys.readouterr().out)
        assert data["matched"] == 1
        assert data["captures"][0]["ecu"] == "BMS"
        assert data["captures"][0]["payload"] == "61F2AABB"
        assert data["captures"][0]["vehicle_states"] == ["ready"]
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


class TestTimeEnforcement:
    """Tranche 2.6 — payload captures always timestamped; validator gate."""

    def test_payload_capture_backfills_time(self):
        s = build_query_session([("0x7EC", "2101", "6101AA", "")], "l", [], "")
        assert s["captures"][0].get("time")  # never untimed

    def test_journal_append_stamps_time(self, tmp_path):
        from canlib.capture_journal import CaptureJournal

        j = CaptureJournal.open(tmp_path, label="L")
        j.append("0x7EC", "2101", "6101AA")  # no explicit time
        j.flush()
        # read back the journal line
        import json

        lines = [
            json.loads(ln) for f in tmp_path.rglob("*.jsonl") for ln in f.read_text().splitlines()
        ]
        cap = next(r for r in lines if r.get("type") == "capture")
        assert cap.get("time")

    def test_validator_warns_missing_time_on_payload(self, tmp_path):
        from canlib.commands.validate import _capture_missing_time_warnings

        doc = {
            "sessions": [
                {
                    "date": "2026-07-22",
                    "captures": [
                        {"ecu": "0x7EC", "pid": "2101", "payload": "6101AA"},  # no time
                        {"ecu": "0x7EC", "pid": "2101", "payload": "6101BB", "time": "09:00:00"},
                        {"ecu": "0x7EC", "pid": "scan 22 0100", "scan_results": {}},  # exempt
                    ],
                }
            ]
        }
        p = tmp_path / "2026-07-22.yaml"
        p.write_text(yaml.safe_dump(doc))
        warns = _capture_missing_time_warnings(p)
        assert len(warns) == 1
        assert "captures[0]" in warns[0]


class TestPairByTime:
    def test_pairs_within_tolerance(self):
        from datetime import datetime

        dts = {0: datetime(2026, 7, 22, 12, 0, 0), 1: datetime(2026, 7, 22, 12, 0, 1)}
        frames = _pair_by_time([0], [1], dts, tol_s=2.5)
        assert frames == [(0, 1)]

    def test_out_of_tolerance_are_singletons_in_time_order(self):
        from datetime import datetime

        # A@00, B@10 — 10s apart, tol 2.5 → each alone, earlier first.
        dts = {0: datetime(2026, 7, 22, 12, 0, 0), 1: datetime(2026, 7, 22, 12, 0, 10)}
        frames = _pair_by_time([0], [1], dts, tol_s=2.5)
        assert frames == [(0, None), (None, 1)]

    def test_leftover_tail_appended(self):
        from datetime import datetime

        # A0@00 pairs B@01; A1@10 has no B left → appended as a left singleton.
        dts = {
            0: datetime(2026, 7, 22, 12, 0, 0),
            2: datetime(2026, 7, 22, 12, 0, 10),
            1: datetime(2026, 7, 22, 12, 0, 1),
        }
        frames = _pair_by_time([0, 2], [1], dts, tol_s=2.5)
        assert frames == [(0, 1), (2, None)]

    def test_empty_sides(self):
        from datetime import datetime

        dts = {0: datetime(2026, 7, 22, 12, 0, 0)}
        assert _pair_by_time([0], [], dts, tol_s=2.5) == [(0, None)]
        assert _pair_by_time([], [0], dts, tol_s=2.5) == [(None, 0)]
        assert _pair_by_time([], [], {}, tol_s=2.5) == []


class TestBuildPairFrames:
    def test_pairs_two_keys_within_tolerance(self):
        caps = [
            _entry(ecu="VCU", pid="2101", payload="6101AA", time="12:00:00"),
            _entry(ecu="BMS", pid="2101", payload="6101BB", time="12:00:01"),
            _entry(ecu="VCU", pid="2101", payload="6101CC", time="12:00:10"),
        ]
        frames, key_a, key_b, n_no_time = _build_pair_frames(caps, tol_s=2.5)
        assert key_a == ("VCU", "2101") and key_b == ("BMS", "2101")
        assert n_no_time == 0
        # VCU@00 pairs BMS@01; VCU@10 is a left-only frame.
        assert frames == [(0, 1), (2, None)]

    def test_tighter_tolerance_splits_the_pair(self):
        caps = [
            _entry(ecu="VCU", pid="2101", payload="6101AA", time="12:00:00"),
            _entry(ecu="BMS", pid="2101", payload="6101BB", time="12:00:01"),
        ]
        frames, _, _, _ = _build_pair_frames(caps, tol_s=0.5)
        assert frames == [(0, None), (None, 1)]

    def test_untimed_captures_excluded_and_counted(self):
        caps = [
            _entry(ecu="VCU", pid="2101", payload="6101AA", time="12:00:00"),
            _entry(ecu="BMS", pid="2101", payload="6101BB", time=""),  # no time
        ]
        frames, _, _, n_no_time = _build_pair_frames(caps, tol_s=2.5)
        assert n_no_time == 1
        assert frames == [(0, None)]


class TestCmdStepPair:
    def test_requires_exactly_two_keys(self, capsys):
        # A single-key query cannot be paired.
        entries = [_entry(ecu="BMS", pid="2101", payload="6101AA", time="12:00:00")]
        cmd_step_pair(entries, "BMS:2101", captures_dir="unused")
        out = capsys.readouterr().out
        assert "two distinct ECU:PID" in out

    def _render(self, caps, tol_s=2.5, frame=0):
        import io

        from rich.console import Console

        frames, key_a, key_b, _ = _build_pair_frames(caps, tol_s=tol_s)
        buf = io.StringIO()
        console = Console(file=buf, highlight=False, width=100)
        _render_step_pair_frame(
            console,
            caps,
            frames,
            frame,
            defs={key_a: ({}, None), key_b: ({}, None)},
            prev_idx=[None] * len(caps),
            ordinals=[(1, 1)] * len(caps),
            key_a=key_a,
            key_b=key_b,
            tol_s=tol_s,
        )
        return buf.getvalue()

    def test_render_pair_frame_shows_both_ecus_and_delta(self):
        caps = [
            _entry(ecu="VCU", pid="2101", payload="6101AA", time="12:00:00"),
            _entry(ecu="BMS", pid="2101", payload="6101BB", time="12:00:01"),
        ]
        text = self._render(caps)
        assert "VCU" in text and "BMS" in text
        assert "pair 1/1" in text
        assert "Δt=1.00s" in text

    def test_render_pair_frame_shows_missing_side(self):
        caps = [
            _entry(ecu="VCU", pid="2101", payload="6101AA", time="12:00:00"),
            _entry(ecu="BMS", pid="2101", payload="6101BB", time="12:00:10"),
        ]
        # First frame is VCU-only (BMS is 10s away, out of tolerance).
        text = self._render(caps, frame=0)
        assert "no BMS:2101 capture within" in text


class TestSetSessionNote:
    """set_session_note: canonical way to edit a session's notes (not hand-edit)."""

    def _write(self, tmp_path):
        from canlib.captures import save_session

        s = build_query_session(
            [("0x7EC", "2101", "6101AA", "12:00:00")], "L", ["ready"], "old note"
        )
        return save_session(s, tmp_path)

    def test_set_note(self, tmp_path):
        from canlib.captures import set_session_note

        f = self._write(tmp_path)
        set_session_note(f, 0, "new note text")
        doc = yaml.safe_load(f.read_text())
        assert doc["sessions"][0]["notes"] == "new note text"

    def test_clear_note(self, tmp_path):
        from canlib.captures import set_session_note

        f = self._write(tmp_path)
        set_session_note(f, 0, "   ")
        doc = yaml.safe_load(f.read_text())
        assert "notes" not in doc["sessions"][0]

    def test_bad_index_raises(self, tmp_path):
        from canlib.captures import set_session_note

        f = self._write(tmp_path)
        with pytest.raises(IndexError):
            set_session_note(f, 5, "x")

    def test_preserves_captures(self, tmp_path):
        from canlib.captures import set_session_note

        f = self._write(tmp_path)
        set_session_note(f, 0, "edited")
        doc = yaml.safe_load(f.read_text())
        assert doc["sessions"][0]["captures"][0]["payload"] == "6101AA"


class TestSetSessionKeepMode:
    """set_session_keep_mode: backfill keep_mode on pre-existing sessions."""

    def _write(self, tmp_path):
        from canlib.captures import save_session

        s = build_query_session([("0x7EC", "2101", "6101AA", "12:00:00")], "L", ["sleep"], "n")
        return save_session(s, tmp_path)

    def test_set_unique(self, tmp_path):
        from canlib.captures import set_session_keep_mode

        f = self._write(tmp_path)
        set_session_keep_mode(f, 0, "unique")
        doc = yaml.safe_load(f.read_text())
        assert doc["sessions"][0]["keep_mode"] == "unique"

    def test_non_unique_clears(self, tmp_path):
        from canlib.captures import set_session_keep_mode

        f = self._write(tmp_path)
        set_session_keep_mode(f, 0, "unique")
        set_session_keep_mode(f, 0, "all")  # not meaningful → cleared
        doc = yaml.safe_load(f.read_text())
        assert "keep_mode" not in doc["sessions"][0]

    def test_bad_index_raises(self, tmp_path):
        from canlib.captures import set_session_keep_mode

        f = self._write(tmp_path)
        with pytest.raises(IndexError):
            set_session_keep_mode(f, 5, "unique")
