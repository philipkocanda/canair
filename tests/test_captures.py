"""Tests for capture session builders and metadata resolution."""

import json
import re
from typing import ClassVar
from unittest.mock import patch

import pytest

from canlib.captures import (
    build_query_session,
    resolve_metadata,
    save_session,
)
from canlib.commands.captures import (
    _clean,
    _print_decoded_preview,
    _quality_tag,
    cmd_diff,
    cmd_latest,
    cmd_list,
    cmd_sessions,
    cmd_summary,
)
from canlib.commands.captures.join import _nearest_within, build_join_frames
from canlib.commands.captures.query import _gather_query, _is_hex_payload, group_sessions

_ANSI_RE = re.compile(r"\x1b\[[0-9;]*[A-Za-z]")


def _strip_ansi(text: str) -> str:
    """Drop colour codes so an assertion can read the words, not the escapes."""
    return _ANSI_RE.sub("", text)


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
        assert meta == ("My label", ["READY", "PARKED"], "some notes")

    def test_label_given_defaults_empty_state_notes(self):
        with patch("builtins.input", side_effect=AssertionError("should not prompt")):
            meta = resolve_metadata("Only label", None, None)
        assert meta == ("Only label", [], "")

    def test_no_label_falls_back_to_prompt(self):
        with patch("builtins.input", side_effect=["Prompted", "charging", "n"]):
            meta = resolve_metadata(None, None, None, suggested_label="sugg")
        assert meta == ("Prompted", ["CHARGING"], "n")

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
        assert cap0["rx"] == "0x7EB" and cap0["pid"] == "2102" and cap0["payload"] == "6102AABB"
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

    def test_keep_mode_changes_persisted(self):
        s = build_query_session([("0x7EC", "2101", "6101", "")], "l", [], "", keep_mode="changes")
        assert s["keep_mode"] == "changes"

    def test_keep_mode_all_not_persisted(self):
        # Only the dedup modes ("changes"/"unique") change interpretation; don't
        # clutter the session with keep-all/last.
        for mode in ("all", "last", None):
            s = build_query_session([("0x7EC", "2101", "6101", "")], "l", [], "", keep_mode=mode)
            assert "keep_mode" not in s

    def test_roundtrips_and_appends_via_save_session(self, tmp_path):
        results = [("0x7EB", "2102", "6102AABB", "")]  # MCU
        s = build_query_session(results, "Live ref", ["ready", "parked"], "18C")
        save_session(s, tmp_path)
        files = list(tmp_path.glob("*.json"))
        assert len(files) == 1
        data = json.loads(files[0].read_text())
        assert data["sessions"][0]["label"] == "Live ref"
        assert data["sessions"][0]["captures"][0]["payload"] == "6102AABB"


class TestSavedBanner:
    """Every save reports its full destination path, not a bare filename."""

    def test_save_session_prints_full_path(self, tmp_path, capsys):
        s = build_query_session([("0x7EB", "2102", "6102AABB", "")], "l", [], "")
        written = save_session(s, tmp_path)
        out = capsys.readouterr().out
        assert str(written) in out
        assert str(tmp_path) in out  # the directory, so the profile is unambiguous
        assert "1 capture(s)" in out

    def test_banner_formats_count_and_path(self, tmp_path):
        from canlib.captures import saved_banner

        line = saved_banner(tmp_path / "2026-08-04.json", 3)
        assert line.strip() == f"→ Saved 3 capture(s) to {tmp_path / '2026-08-04.json'}"


def _entry(**kw):
    """A minimal flat capture entry as load_all_captures would produce."""
    base = {
        "file": "2026-07-22.json",
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
        sessions = group_sessions(entries)
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
        assert len(group_sessions(entries)) == 2

    def test_chronological_order(self):
        entries = [
            _entry(_session_idx=0, date="2026-07-22", time="18:00:00"),
            _entry(_session_idx=1, date="2026-07-20", time="09:00:00"),
        ]
        sessions = group_sessions(entries)
        assert [s["date"] for s in sessions] == ["2026-07-20", "2026-07-22"]

    def test_distinct_capture_notes_deduped(self):
        entries = [
            _entry(_session_idx=0, notes="note X"),
            _entry(_session_idx=0, notes="note X"),
            _entry(_session_idx=0, notes="note Y"),
        ]
        assert group_sessions(entries)[0]["cap_notes"] == ["note X", "note Y"]


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

    def test_capture_notes_listed_and_truncated(self, capsys):
        # Distinct capture-level notes are listed, capped at max_notes with a
        # "+N more" tail so the cap is never silent.
        entries = [
            _entry(session_label="rich", time=f"10:00:0{i}", notes=f"note {i}") for i in range(5)
        ]
        cmd_sessions(entries, max_notes=2)
        out = capsys.readouterr().out
        assert "note 0" in out and "note 1" in out
        assert "note 2" not in out
        assert "+3 more capture-notes" in out

    def test_long_capture_note_is_shortened(self, capsys):
        entries = [_entry(session_label="long", time="10:00:00", notes="x" * 200)]
        cmd_sessions(entries)
        out = capsys.readouterr().out
        assert "x" * 97 + "..." in out
        assert "x" * 101 not in out


class TestQualityTag:
    """The transport-health footprint shown per session in the TOC."""

    def test_clean_session_is_silent(self):
        # Only a session that recorded trouble gets a line; the TOC stays terse.
        assert _quality_tag({"exchanges": 42}) == ""
        assert _quality_tag({}) == ""
        assert _quality_tag(None) == ""

    def test_drops_and_stale_are_summed(self):
        tag = _strip_ansi(_quality_tag({"drop": 2, "stale": 3, "exchanges": 100}))
        assert "drops 5" in tag
        assert "100 exchanges" in tag
        assert "errors" not in tag

    def test_other_categories_reported_as_errors(self):
        # no_data/bus/decode/other are non-answers, not ISO-TP integrity failures.
        tag = _strip_ansi(_quality_tag({"no_data": 1, "bus": 2, "decode": 3, "other": 4}))
        assert "errors 10" in tag
        assert "drops" not in tag

    def test_exchange_total_omitted_when_absent(self):
        assert "exchanges" not in _strip_ansi(_quality_tag({"drop": 1}))

    def test_shown_in_sessions_text(self, capsys):
        entries = [_entry(session_label="flaky", time="10:00:00", quality={"drop": 1})]
        cmd_sessions(entries)
        assert "drops 1" in _strip_ansi(capsys.readouterr().out)


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


class TestCmdListLimit:
    def _many(self, n):
        # n captures for one PID, chronologically increasing timestamps.
        return [
            _entry(ecu="BMS", pid="2101", payload=f"6101{i:02X}", time=f"12:00:{i:02d}")
            for i in range(n)
        ]

    def test_json_truncates_to_latest_n(self, capsys):
        import json

        cmd_list(self._many(10), "BMS:2101", as_json=True, limit=3)
        data = json.loads(capsys.readouterr().out)
        assert data["matched"] == 10
        assert data["shown"] == 3
        assert data["truncated"] is True
        assert data["limit"] == 3
        # Keeps the most recent 3 (tail of the chronological list).
        assert [c["payload"] for c in data["captures"]] == ["610107", "610108", "610109"]

    def test_json_no_cap_when_limit_zero(self, capsys):
        import json

        cmd_list(self._many(10), "BMS:2101", as_json=True, limit=0)
        data = json.loads(capsys.readouterr().out)
        assert data["matched"] == 10
        assert data["shown"] == 10
        assert data["truncated"] is False

    def test_json_not_truncated_when_under_limit(self, capsys):
        import json

        cmd_list(self._many(3), "BMS:2101", as_json=True, limit=50)
        data = json.loads(capsys.readouterr().out)
        assert data["shown"] == 3 and data["truncated"] is False

    def test_text_footer_reports_hidden_history(self, capsys):
        cmd_list(self._many(10), "BMS:2101", as_json=False, limit=3)
        out = capsys.readouterr().out
        # Loud, always-printed truncation notice (not TTY-gated).
        assert "7 more not shown" in out
        assert "--limit 0" in out
        assert "latest 3 of 10" in out


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
                        {"rx": "0x7EC", "pid": "2101", "payload": "6101AA"},  # no time
                        {"rx": "0x7EC", "pid": "2101", "payload": "6101BB", "time": "09:00:00"},
                        {"rx": "0x7EC", "pid": "scan 22 0100", "scan_results": {}},  # exempt
                    ],
                }
            ]
        }
        p = tmp_path / "2026-07-22.json"
        p.write_text(json.dumps(doc))
        warns = _capture_missing_time_warnings(p)
        assert len(warns) == 1
        assert "captures[0]" in str(warns[0])


class TestNearestWithin:
    """The join's nearest-within-tolerance rule must match canlib.align's."""

    def test_picks_nearest_and_respects_tolerance(self):
        ts = [0.0, 10.0, 20.0]
        assert _nearest_within(ts, 9.0, 2.0) == 1
        assert _nearest_within(ts, 12.0, 2.0) == 1
        assert _nearest_within(ts, 5.0, 2.0) is None  # 5s from both, tol 2
        assert _nearest_within([], 1.0, 5.0) is None

    def test_exact_tie_prefers_the_earlier_sample(self):
        # 5.0 is equidistant from 0 and 10; align's rule keeps the earlier one.
        assert _nearest_within([0.0, 10.0], 5.0, 10.0) == 0

    def test_parity_with_align_join_prepared(self):
        """Same inputs, same pairing decisions as canlib.align."""
        from datetime import datetime, timedelta

        from canlib.align import TimePoint, join_prepared, prepare_series

        t0 = datetime(2026, 7, 22, 12, 0, 0)
        ref_offsets = [0.0, 3.0, 9.0, 30.0]
        cand_offsets = [0.4, 4.0, 20.0]
        tol = 2.5

        ref = prepare_series([TimePoint(t0 + timedelta(seconds=o), o) for o in ref_offsets])
        cand = prepare_series([TimePoint(t0 + timedelta(seconds=o), o) for o in cand_offsets])
        _rv, cand_vals, n_dropped = join_prepared(ref, cand, tol_s=tol)

        cand_ts = [t0.timestamp() + o for o in cand_offsets]
        mine = [_nearest_within(cand_ts, t0.timestamp() + o, tol) for o in ref_offsets]

        # align drops unmatched rows; ours yields None in the same places.
        assert n_dropped == sum(1 for m in mine if m is None)
        assert [cand_offsets[m] for m in mine if m is not None] == cand_vals


class TestBuildJoinFrames:
    def test_single_key_yields_one_frame_per_capture(self):
        caps = [
            _entry(ecu="VCU", pid="2101", payload="6101AA", time="12:00:00"),
            _entry(ecu="VCU", pid="2101", payload="6101BB", time="12:00:05"),
        ]
        frames, n_no_time = build_join_frames(caps, [("VCU", "2101")], tol_s=2.5)
        assert n_no_time == 0
        assert [f.indices for f in frames] == [(0,), (1,)]

    def test_two_keys_within_tolerance_collapse_to_one_frame(self):
        caps = [
            _entry(ecu="VCU", pid="2101", payload="6101AA", time="12:00:00"),
            _entry(ecu="BMS", pid="2101", payload="6101BB", time="12:00:01"),
            _entry(ecu="VCU", pid="2101", payload="6101CC", time="12:00:10"),
        ]
        keys = [("VCU", "2101"), ("BMS", "2101")]
        frames, _ = build_join_frames(caps, keys, tol_s=2.5)
        # VCU@00 + BMS@01 join (both anchors give the same pair -> one frame);
        # VCU@10 has no BMS in range.
        assert [f.indices for f in frames] == [(0, 1), (2, None)]

    def test_three_keys_stack_in_key_order(self):
        caps = [
            _entry(ecu="HVAC", pid="220100", payload="6201AA", time="12:00:00"),
            _entry(ecu="HVAC", pid="2201A0", payload="6201BB", time="12:00:01"),
            _entry(ecu="HVAC", pid="2201A2", payload="6201CC", time="12:00:02"),
        ]
        keys = [("HVAC", "220100"), ("HVAC", "2201A0"), ("HVAC", "2201A2")]
        frames, _ = build_join_frames(caps, keys, tol_s=5.0)
        assert [f.indices for f in frames] == [(0, 1, 2)]
        # Reordering the keys reorders the slots, not the pairing.
        frames, _ = build_join_frames(caps, list(reversed(keys)), tol_s=5.0)
        assert [f.indices for f in frames] == [(2, 1, 0)]

    def test_tight_tolerance_hides_nothing(self):
        caps = [
            _entry(ecu="VCU", pid="2101", payload="6101AA", time="12:00:00"),
            _entry(ecu="BMS", pid="2101", payload="6101BB", time="12:00:01"),
        ]
        keys = [("VCU", "2101"), ("BMS", "2101")]
        frames, _ = build_join_frames(caps, keys, tol_s=0.5)
        # Every capture still anchors a frame — just no longer joined.
        assert [f.indices for f in frames] == [(0, None), (None, 1)]

    def test_repeating_set_is_not_collapsed_across_time(self):
        caps = [
            _entry(ecu="VCU", pid="2101", payload="6101AA", time="12:00:00"),
            _entry(ecu="BMS", pid="2101", payload="6101BB", time="12:00:01"),
            _entry(ecu="VCU", pid="2101", payload="6101CC", time="12:01:00"),
            _entry(ecu="BMS", pid="2101", payload="6101DD", time="12:01:01"),
        ]
        keys = [("VCU", "2101"), ("BMS", "2101")]
        frames, _ = build_join_frames(caps, keys, tol_s=2.5)
        assert [f.indices for f in frames] == [(0, 1), (2, 3)]

    def test_untimed_captures_excluded_and_counted(self):
        caps = [
            _entry(ecu="VCU", pid="2101", payload="6101AA", time="12:00:00"),
            _entry(ecu="BMS", pid="2101", payload="6101BB", time=""),  # no time
        ]
        keys = [("VCU", "2101"), ("BMS", "2101")]
        frames, n_no_time = build_join_frames(caps, keys, tol_s=2.5)
        assert n_no_time == 1
        assert [f.indices for f in frames] == [(0, None)]

    def test_key_with_no_captures_is_an_empty_slot(self):
        caps = [_entry(ecu="VCU", pid="2101", payload="6101AA", time="12:00:00")]
        keys = [("VCU", "2101"), ("BMS", "2101")]
        frames, _ = build_join_frames(caps, keys, tol_s=2.5)
        assert [f.indices for f in frames] == [(0, None)]

    def test_anchor_time_is_the_anchoring_capture(self):
        from datetime import datetime

        caps = [_entry(ecu="VCU", pid="2101", payload="6101AA", time="12:00:00")]
        frames, _ = build_join_frames(caps, [("VCU", "2101")], tol_s=2.5)
        assert frames[0].anchor_dt == datetime(2026, 7, 22, 12, 0, 0)


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
        doc = json.loads(f.read_text())
        assert doc["sessions"][0]["notes"] == "new note text"

    def test_clear_note(self, tmp_path):
        from canlib.captures import set_session_note

        f = self._write(tmp_path)
        set_session_note(f, 0, "   ")
        doc = json.loads(f.read_text())
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
        doc = json.loads(f.read_text())
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
        doc = json.loads(f.read_text())
        assert doc["sessions"][0]["keep_mode"] == "unique"

    def test_non_unique_clears(self, tmp_path):
        from canlib.captures import set_session_keep_mode

        f = self._write(tmp_path)
        set_session_keep_mode(f, 0, "unique")
        set_session_keep_mode(f, 0, "all")  # not meaningful → cleared
        doc = json.loads(f.read_text())
        assert "keep_mode" not in doc["sessions"][0]

    def test_bad_index_raises(self, tmp_path):
        from canlib.captures import set_session_keep_mode

        f = self._write(tmp_path)
        with pytest.raises(IndexError):
            set_session_keep_mode(f, 5, "unique")


class TestSetSessionStates:
    """set_session_states: canonical way to back-fill a session's vehicle_states."""

    def _write(self, tmp_path, states=None):
        from canlib.captures import save_session

        s = build_query_session([("0x7EC", "2101", "6101AA", "12:00:00")], "L", states or [], "n")
        return save_session(s, tmp_path)

    def test_backfill_from_empty(self, tmp_path):
        from canlib.captures import set_session_states

        f = self._write(tmp_path, states=[])
        set_session_states(f, 0, "charging")
        doc = json.loads(f.read_text())
        assert doc["sessions"][0]["vehicle_states"] == ["CHARGING"]

    def test_accepts_comma_string_and_list(self, tmp_path):
        from canlib.captures import set_session_states

        f = self._write(tmp_path, states=[])
        set_session_states(f, 0, "charging, ready")
        doc = json.loads(f.read_text())
        assert doc["sessions"][0]["vehicle_states"] == ["CHARGING", "READY"]

    def test_empty_clears(self, tmp_path):
        from canlib.captures import set_session_states

        f = self._write(tmp_path, states=["charging"])
        set_session_states(f, 0, "")
        doc = json.loads(f.read_text())
        assert "vehicle_states" not in doc["sessions"][0]

    def test_bad_index_raises(self, tmp_path):
        from canlib.captures import set_session_states

        f = self._write(tmp_path)
        with pytest.raises(IndexError):
            set_session_states(f, 5, "charging")

    def test_preserves_captures(self, tmp_path):
        from canlib.captures import set_session_states

        f = self._write(tmp_path, states=[])
        set_session_states(f, 0, "charging")
        doc = json.loads(f.read_text())
        assert doc["sessions"][0]["captures"][0]["payload"] == "6101AA"


class TestCmdDelete:
    """`canair captures uds --delete <query>` — targeted capture removal."""

    def _seed(self, cdir):
        # MCU (0x7EB): two 2102 captures + one 2101 capture in one session.
        s = build_query_session(
            [
                ("0x7EB", "2102", "6102AABB", ""),
                ("0x7EB", "2102", "6102CCDD", ""),
                ("0x7EB", "2101", "6101EEFF", ""),
            ],
            "test",
            ["ready"],
            "",
        )
        save_session(s, cdir)

    def test_dry_run_deletes_nothing(self, tmp_path, capsys):
        from canlib.commands.captures import cmd_delete
        from canlib.commands.captures.query import load_all_captures

        self._seed(tmp_path)
        entries = load_all_captures(tmp_path)
        rc = cmd_delete(entries, "MCU:2102", captures_dir=tmp_path, dry_run=True)
        out = capsys.readouterr().out
        assert rc == 0
        assert "Would delete 2 capture(s)" in out
        # File untouched: all 3 captures still present.
        assert len(load_all_captures(tmp_path)) == 3

    def test_deletes_matching_only(self, tmp_path):
        from canlib.commands.captures import cmd_delete
        from canlib.commands.captures.query import load_all_captures

        self._seed(tmp_path)
        entries = load_all_captures(tmp_path)
        rc = cmd_delete(entries, "MCU:2102", captures_dir=tmp_path, assume_yes=True)
        assert rc == 0
        remaining = load_all_captures(tmp_path)
        # The two 2102 captures are gone; the 2101 one survives.
        assert [(e["ecu"], str(e["pid"])) for e in remaining] == [("MCU", "2101")]

    def test_no_match_returns_1(self, tmp_path, capsys):
        from canlib.commands.captures import cmd_delete
        from canlib.commands.captures.query import load_all_captures

        self._seed(tmp_path)
        entries = load_all_captures(tmp_path)
        rc = cmd_delete(entries, "MCU:9999", captures_dir=tmp_path, assume_yes=True)
        assert rc == 1
        assert "nothing to delete" in capsys.readouterr().out
        assert len(load_all_captures(tmp_path)) == 3

    def test_json_dry_run_emits_rows(self, tmp_path, capsys):
        from canlib.commands.captures import cmd_delete
        from canlib.commands.captures.query import load_all_captures

        self._seed(tmp_path)
        entries = load_all_captures(tmp_path)
        capsys.readouterr()  # discard save_session's "Saved N capture(s)" banner
        rc = cmd_delete(entries, "MCU:2102", captures_dir=tmp_path, dry_run=True, as_json=True)
        assert rc == 0
        rows = json.loads(capsys.readouterr().out)
        assert len(rows) == 2
        assert all(r["pid"] == "2102" for r in rows)


class TestCmdBackfillStates:
    """`canair captures uds --backfill-states` — offline state inference back-fill.

    Uses the pinned real ``ioniq-2017`` profile (its vehicle_states.yaml carries
    the CHARGING/READY/PARKED/… predicates) with real captured payloads that
    decode to known state signals.
    """

    # VCU:2101, EV_READY=1 & GEAR_PARK=1 → READY, PARKED.
    _VCU_READY_PARK = "6101ffe0000009215a5e064803000000009478340420"
    # VCU:2101, EV_READY=0 & GEAR_PARK=1 → PARKED (and READY provably false).
    _VCU_PARK_NOT_READY = "6101FFE000000921106A064A03000000008E770007200000000000"

    def _seed(self, cdir, payload, states, *, rx="0x7EA", pid="2101"):
        s = build_query_session([(rx, pid, payload, "")], "test", states, "")
        save_session(s, cdir)

    def _states(self, cdir):
        files = list(cdir.glob("*.json"))
        assert len(files) == 1
        return json.loads(files[0].read_text())["sessions"][0].get("vehicle_states")

    def _run(self, cdir, **kw):
        from canlib.commands.captures.backfill import cmd_backfill_states
        from canlib.commands.captures.query import load_all_captures

        return cmd_backfill_states(load_all_captures(cdir), captures_dir=cdir, **kw)

    def test_fill_from_empty(self, tmp_path):
        self._seed(tmp_path, self._VCU_READY_PARK, [])
        rc = self._run(tmp_path, assume_yes=True)
        assert rc == 0
        assert self._states(tmp_path) == ["READY", "PARKED"]

    def test_conflict_not_written_by_default(self, tmp_path, capsys):
        self._seed(tmp_path, self._VCU_PARK_NOT_READY, ["ready"])
        rc = self._run(tmp_path, assume_yes=True)
        out = capsys.readouterr().out
        assert rc == 0
        assert "conflict" in out
        # A conflicting recorded state is reported but left untouched.
        assert self._states(tmp_path) == ["ready"]

    def test_overwrite_corrects_conflict(self, tmp_path):
        self._seed(tmp_path, self._VCU_PARK_NOT_READY, ["ready"])
        rc = self._run(tmp_path, overwrite=True, assume_yes=True)
        assert rc == 0
        result = self._states(tmp_path)
        # READY was provably false → dropped; PARKED (matched) written.
        assert "READY" not in result
        assert "PARKED" in result

    def test_dry_run_writes_nothing(self, tmp_path, capsys):
        self._seed(tmp_path, self._VCU_READY_PARK, [])
        rc = self._run(tmp_path, dry_run=True, assume_yes=True)
        assert rc == 0
        assert "--dry-run" in capsys.readouterr().out
        assert self._states(tmp_path) is None  # unchanged (empty → field absent)

    def test_json_dry_run_emits_rows(self, tmp_path, capsys):
        self._seed(tmp_path, self._VCU_READY_PARK, [])
        capsys.readouterr()  # discard save banner
        rc = self._run(tmp_path, dry_run=True, as_json=True)
        assert rc == 0
        rows = json.loads(capsys.readouterr().out)
        assert len(rows) == 1
        assert rows[0]["verdict"] == "fill"
        assert rows[0]["inferred"] == ["READY", "PARKED"]

    def test_non_tty_without_yes_refuses(self, tmp_path, capsys):
        self._seed(tmp_path, self._VCU_READY_PARK, [])
        # pytest runs with a non-interactive stdin/stdout → refuse without --yes.
        rc = self._run(tmp_path, assume_yes=False)
        assert rc == 2
        assert "refusing to write" in capsys.readouterr().err
        assert self._states(tmp_path) is None  # nothing written


class TestCmdSetState:
    """`canair captures uds --set-state STATES` — manual session-state tagging."""

    def _seed(self, cdir, states):
        s = build_query_session([("0x7EB", "2101", "6101EEFF", "")], "body read", states, "")
        save_session(s, cdir)

    def _states(self, cdir):
        files = list(cdir.glob("*.json"))
        assert len(files) == 1
        return json.loads(files[0].read_text())["sessions"][0].get("vehicle_states")

    def _run(self, cdir, states_arg, **kw):
        from canlib.commands.captures.query import load_all_captures
        from canlib.commands.captures.set_state import cmd_set_state

        return cmd_set_state(load_all_captures(cdir), states_arg, captures_dir=cdir, **kw)

    def test_sets_state(self, tmp_path):
        self._seed(tmp_path, [])
        rc = self._run(tmp_path, "ACC", assume_yes=True)
        assert rc == 0
        assert self._states(tmp_path) == ["ACC"]

    def test_multi_token(self, tmp_path):
        self._seed(tmp_path, [])
        rc = self._run(tmp_path, "acc2, parked", assume_yes=True)
        assert rc == 0
        assert self._states(tmp_path) == ["ACC2", "PARKED"]

    def test_dry_run_writes_nothing(self, tmp_path, capsys):
        self._seed(tmp_path, [])
        rc = self._run(tmp_path, "ACC", dry_run=True, assume_yes=True)
        assert rc == 0
        assert "--dry-run" in capsys.readouterr().out
        assert self._states(tmp_path) is None

    def test_empty_states_refused(self, tmp_path, capsys):
        self._seed(tmp_path, [])
        rc = self._run(tmp_path, "", assume_yes=True)
        assert rc == 2
        assert "at least one state" in capsys.readouterr().err
        assert self._states(tmp_path) is None

    def test_already_set_no_write(self, tmp_path, capsys):
        self._seed(tmp_path, ["ACC"])
        rc = self._run(tmp_path, "acc", assume_yes=True)
        assert rc == 0
        assert "0 of 1" in capsys.readouterr().out  # nothing to write
        assert self._states(tmp_path) == ["ACC"]

    def test_out_of_vocab_warns_but_writes(self, tmp_path, capsys):
        self._seed(tmp_path, [])
        rc = self._run(tmp_path, "MADE_UP", assume_yes=True)
        assert rc == 0
        assert "not in the vehicle_states.yaml" in capsys.readouterr().out
        assert self._states(tmp_path) == ["MADE_UP"]

    def test_json_dry_run_emits_rows(self, tmp_path, capsys):
        self._seed(tmp_path, [])
        capsys.readouterr()  # discard save banner
        rc = self._run(tmp_path, "ACC", dry_run=True, as_json=True)
        assert rc == 0
        rows = json.loads(capsys.readouterr().out)
        assert len(rows) == 1
        assert rows[0]["new_states"] == ["ACC"]
        assert rows[0]["will_write"] is True

    def test_non_tty_without_yes_refuses(self, tmp_path, capsys):
        self._seed(tmp_path, [])
        rc = self._run(tmp_path, "ACC", assume_yes=False)
        assert rc == 2
        assert "refusing to set state" in capsys.readouterr().err
        assert self._states(tmp_path) is None

    def test_bare_scope_refused_via_parser(self, tmp_path, capsys):
        # The scope guard lives in run(): --set-state with no scope filter refuses.
        import argparse

        from canlib.commands import captures as cap

        self._seed(tmp_path, [])
        p = cap.add_parser(argparse.ArgumentParser().add_subparsers())
        args = p.parse_args(["uds", "--set-state", "ACC", "--dir", str(tmp_path)])
        rc = cap.run(args)
        assert rc == 2
        assert "requires a scope filter" in capsys.readouterr().err
        assert self._states(tmp_path) is None


def _uds_response(**kw):
    """Build a UdsResponse dict with the fields the builders read."""
    base = {"ok": False, "hex": "", "bytes": b"", "nrc": None, "nrc_desc": "", "error": ""}
    base.update(kw)
    return base


class TestBuildRawSession:
    """build_raw_session: one capture from a single raw UDS response."""

    def test_ok_stores_payload(self):
        from canlib.captures import build_raw_session

        resp = _uds_response(ok=True, hex="62b00412", bytes=b"\x62\xb0\x04\x12")
        s = build_raw_session("0x7EC", 0x7E4, "22B004", resp, "L", ["ready"], "note")
        cap = s["captures"][0]
        assert cap == {"rx": "0x7EC", "pid": "22B004", "payload": "62B00412"}
        assert s["vehicle_states"] == ["ready"]
        assert s["notes"] == "note"

    def test_nrc_recorded_as_response(self):
        from canlib.captures import build_raw_session

        resp = _uds_response(nrc=0x31, nrc_desc="requestOutOfRange")
        s = build_raw_session("0x7EC", 0x7E4, "22B004", resp, "L", [], "")
        cap = s["captures"][0]
        assert cap["response"] == "NRC 0x31 (requestOutOfRange)"
        assert "payload" not in cap
        # empty states/notes are omitted, not stored empty
        assert "vehicle_states" not in s
        assert "notes" not in s

    def test_error_recorded_as_response(self):
        from canlib.captures import build_raw_session

        resp = _uds_response(error="NO DATA")
        s = build_raw_session("0x7EC", 0x7E4, "22B004", resp, "L", [], "")
        assert s["captures"][0]["response"] == "NO DATA"


class TestBuildScanSession:
    """build_scan_session: one scan_results capture summarising a range probe."""

    def test_responding_and_rejected(self):
        from canlib.captures import build_scan_session

        pos = [(0xB004, _uds_response(ok=True, hex="62B004" + "12" * 60, bytes=b"\x00" * 62))]
        neg = [(0xB005, 0x31, "requestOutOfRange")]
        errs = [(0xB006, "timeout")]
        s = build_scan_session(
            "0x7EC", 0x7E4, 0x22, (0xB000, 0xB010), pos, neg, errs, "L", ["ready"], "n"
        )
        cap = s["captures"][0]
        assert cap["pid"] == "scan 22 B000-B010"
        sr = cap["scan_results"]
        assert sr["responding"][0]["did"] == "B004"
        assert sr["responding"][0]["response"] == "62 bytes"
        # Long raw hex is truncated with an ellipsis.
        assert sr["responding"][0]["notes"].endswith("...")
        assert sr["rejected"] == "2 DIDs returned 1 NRC + 1 errors"

    def test_service_21_uses_two_digit_did(self):
        from canlib.captures import build_scan_session

        s = build_scan_session("0x7EC", 0x7E4, 0x21, (0x01, 0x02), [], [], [], "L", [], "")
        assert s["captures"][0]["pid"] == "scan 21 01-02"

    def test_append_bytes_suffix(self):
        from canlib.captures import build_scan_session

        s = build_scan_session(
            "0x7EC", 0x7E4, 0x22, (0xB000, 0xB010), [], [], [], "L", [], "", append_bytes="AA"
        )
        assert s["captures"][0]["pid"] == "scan 22 B000-B010 + suffix AA"

    def test_no_rejected_when_all_positive(self):
        from canlib.captures import build_scan_session

        pos = [(0xB004, _uds_response(ok=True, hex="62B004", bytes=b"\x62\xb0\x04"))]
        s = build_scan_session("0x7EC", 0x7E4, 0x22, (0xB000, 0xB010), pos, [], [], "L", [], "")
        assert "rejected" not in s["captures"][0]["scan_results"]


class TestBuildDiscoverSession:
    """build_discover_session: broadcast-rx capture from a discovery sweep."""

    def test_alive_and_silent(self):
        from canlib.captures import build_discover_session

        alive = [(0x7E0, "ECU-A", "50 03"), (0x7E2, "ECU-B", "")]
        s = build_discover_session(alive, 5, 2, (0x700, 0x7FF), "L", ["ready"], "n")
        cap = s["captures"][0]
        assert cap["rx"] == "broadcast"
        assert cap["pid"] == "discover 700-7FF"
        responding = cap["scan_results"]["responding"]
        # Each responder's originating ECU is its CAN response address (TX + 8).
        assert responding[0]["rx"] == "0x7E8"
        assert responding[0]["response"] == "ECU-A"
        assert responding[0]["notes"] == "Raw: 50 03"
        assert responding[1]["notes"] == ""
        # silent + error counts fold into one rejected line.
        assert cap["scan_results"]["rejected"] == "7 addresses silent"

    def test_no_responders(self):
        from canlib.captures import build_discover_session

        s = build_discover_session([], 3, 0, (0x700, 0x710), "L", [], "")
        sr = s["captures"][0]["scan_results"]
        assert "responding" not in sr
        assert sr["rejected"] == "3 addresses silent"


class TestSessionFieldOrder:
    """On-disk field order is deliberate: metadata first, the big array last.

    All five builders assemble a `CaptureSession` as a typed literal with the
    optional metadata splatted in (`{"date": …, "label": …, **meta, "captures":
    …}`) precisely so every field write is checked *without* moving `captures`
    ahead of the small metadata fields — the readability property that made the
    obvious "type the literal directly" approach unusable. Nothing enforces that
    ordering but this test.
    """

    _EXPECTED: ClassVar[list[str]] = [
        "date",
        "label",
        "vehicle_states",
        "notes",
        "keep_mode",
        "transport",
        "quality",
    ]

    def _order(self, session):
        keys = [k for k in session if k != "captures"]
        assert list(session)[-1] == "captures", "captures must be the last field"
        return keys

    def test_query_session_full(self):
        s = build_query_session(
            [("0x7EC", "2101", "6101AA", "12:00:00")],
            "L",
            ["READY"],
            "n",
            keep_mode="changes",
            date="2026-08-04",
            transport="slcan-tcp",
            quality={"exchanges": 3, "drop": 1},
        )
        assert self._order(s) == self._EXPECTED

    def test_omitted_metadata_leaves_no_gap(self):
        s = build_query_session([("0x7EC", "2101", "6101AA", "12:00:00")], "L", [], "")
        assert self._order(s) == ["date", "label"]

    def test_every_builder_puts_captures_last(self):
        from canlib.captures import (
            build_discover_session,
            build_manual_session,
            build_raw_session,
            build_scan_session,
        )

        record = {"rx": "0x7EC", "pid": "2101", "payload": "6101"}
        base = ["date", "label", "vehicle_states", "notes"]
        cases = {
            # build_manual_session defaults transport to "import".
            "manual": (
                build_manual_session([record], label="L", vehicle_states=["READY"], notes="n"),
                [*base, "transport"],
            ),
            "raw": (
                build_raw_session(
                    "0x7EC",
                    0x7E4,
                    "2101",
                    _uds_response(ok=True, hex="6101", bytes=b"\x61\x01"),
                    "L",
                    ["READY"],
                    "n",
                ),
                base,
            ),
            "discover": (
                build_discover_session(
                    [(0x7E0, "ECU-A", "")], 1, 0, (0x700, 0x7FF), "L", ["READY"], "n"
                ),
                base,
            ),
            "scan": (
                build_scan_session(
                    "0x7EC", 0x7E4, 0x22, (0xB000, 0xB010), [], [], [], "L", ["READY"], "n"
                ),
                base,
            ),
        }
        for name, (session, expected) in cases.items():
            assert self._order(session) == expected, name


class TestSetCaptureNote:
    """set_capture_note: per-capture note editing (not hand-edit)."""

    def _write(self, tmp_path):
        from canlib.captures import save_session

        s = build_query_session(
            [
                ("0x7EC", "2101", "6101AA", "12:00:00"),
                ("0x7EC", "2102", "6102BB", "12:00:01"),
            ],
            "L",
            ["ready"],
            "n",
        )
        return save_session(s, tmp_path)

    def test_set_note(self, tmp_path):
        from canlib.captures import set_capture_note

        f = self._write(tmp_path)
        set_capture_note(f, 0, 1, "second cap note")
        doc = json.loads(f.read_text())
        assert doc["sessions"][0]["captures"][1]["notes"] == "second cap note"
        # sibling capture untouched
        assert "notes" not in doc["sessions"][0]["captures"][0]

    def test_clear_note(self, tmp_path):
        from canlib.captures import set_capture_note

        f = self._write(tmp_path)
        set_capture_note(f, 0, 0, "x")
        set_capture_note(f, 0, 0, "  ")
        doc = json.loads(f.read_text())
        assert "notes" not in doc["sessions"][0]["captures"][0]

    def test_bad_index_raises(self, tmp_path):
        from canlib.captures import set_capture_note

        f = self._write(tmp_path)
        with pytest.raises(IndexError):
            set_capture_note(f, 0, 9, "x")


class TestDeleteCapture:
    """delete_capture: index-addressed removal, dropping an emptied session."""

    def _write(self, tmp_path):
        from canlib.captures import save_session

        s = build_query_session(
            [
                ("0x7EC", "2101", "6101AA", "12:00:00"),
                ("0x7EC", "2102", "6102BB", "12:00:01"),
            ],
            "L",
            ["ready"],
            "n",
        )
        return save_session(s, tmp_path)

    def test_delete_leaves_session_when_more_remain(self, tmp_path):
        from canlib.captures import delete_capture

        f = self._write(tmp_path)
        removed_session = delete_capture(f, 0, 0)
        assert removed_session is False
        doc = json.loads(f.read_text())
        assert len(doc["sessions"]) == 1
        assert len(doc["sessions"][0]["captures"]) == 1
        assert doc["sessions"][0]["captures"][0]["pid"] == "2102"

    def test_delete_last_capture_removes_session(self, tmp_path):
        from canlib.captures import delete_capture

        f = self._write(tmp_path)
        delete_capture(f, 0, 0)
        removed_session = delete_capture(f, 0, 0)
        assert removed_session is True
        doc = json.loads(f.read_text())
        assert doc["sessions"] == []

    def test_bad_index_raises(self, tmp_path):
        from canlib.captures import delete_capture

        f = self._write(tmp_path)
        with pytest.raises(IndexError):
            delete_capture(f, 0, 9)


class TestLabelSuggesters:
    """suggest_*_label: human-readable session labels."""

    def test_scan_label_wide_did(self):
        from canlib.captures import suggest_scan_label

        assert suggest_scan_label("BMS", 0x22, (0xB000, 0xB010)) == "Scan BMS 22 B000-B010"

    def test_scan_label_two_digit_did_and_suffix(self):
        from canlib.captures import suggest_scan_label

        assert suggest_scan_label("BMS", 0x21, (0x01, 0x02), "AA") == "Scan BMS 21 01-02 +AA"

    def test_raw_label(self):
        from canlib.captures import suggest_raw_label

        assert suggest_raw_label("BMS", "2101") == "Raw BMS 2101"

    def test_discover_label(self):
        from canlib.captures import suggest_discover_label

        assert suggest_discover_label((0x700, 0x7FF)) == "Discovery scan 700-7FF"


class TestDecodePayload:
    """_decode_payload: on-demand decode of a stored payload for display."""

    _PIDS: ClassVar[dict] = {
        "ecus": {
            "BMS": {
                "tx_id": 0x7E4,
                "pids": {
                    "2101": {
                        "parameters": {
                            "SOC": {"expression": "B03/2", "unit": "%"},
                            "FLAG": {
                                "expression": "B03",
                                "unit": "",
                                "display": "'HI' if v > 100 else 'LO'",
                            },
                        }
                    }
                },
            }
        }
    }

    def test_decodes_params_and_display(self):
        from canlib.captures import _decode_payload

        # WiCAN layout of 6101C8: B00=PCI, B01=SID, B02=PID, B03=0xC8 (200)
        decoded = _decode_payload("BMS", "2101", "6101C8", self._PIDS)
        assert decoded == {"SOC": "100.0 %", "FLAG": "200.0  (HI)"}

    def test_bad_display_falls_back_to_plain_value(self):
        from canlib.captures import _decode_payload

        pids = {
            "ecus": {
                "BMS": {
                    "tx_id": 0x7E4,
                    "pids": {
                        "2101": {"parameters": {"X": {"expression": "B03/2", "display": "("}}}
                    },
                }
            }
        }
        assert _decode_payload("BMS", "2101", "6101C8", pids) == {"X": "100.0"}

    def test_unknown_ecu_returns_none(self):
        from canlib.captures import _decode_payload

        assert _decode_payload("NOPE", "2101", "6101C8", self._PIDS) is None

    def test_unknown_pid_returns_none(self):
        from canlib.captures import _decode_payload

        assert _decode_payload("BMS", "9999", "6101C8", self._PIDS) is None

    def test_pid_without_parameters_returns_none(self):
        from canlib.captures import _decode_payload

        pids = {"ecus": {"BMS": {"tx_id": 0x7E4, "pids": {"2101": {}}}}}
        assert _decode_payload("BMS", "2101", "6101C8", pids) is None


class TestPrintDecodedPreview:
    """The decoded-param preview caps output but must make the cap visible."""

    def test_under_limit_no_hint(self, capsys):
        _print_decoded_preview({"A": 1, "B": 2}, limit=3, ecu="HVAC", pid="2201A0")
        out = capsys.readouterr().out
        assert "A: 1" in out and "B: 2" in out
        assert "more param" not in out

    def test_over_limit_shows_count_and_decode_hint(self, capsys):
        decoded = {f"P{i}": i for i in range(8)}
        _print_decoded_preview(decoded, limit=3, ecu="HVAC", pid="2201A0")
        out = capsys.readouterr().out
        # only the first `limit` are printed
        assert "P0: 0" in out and "P2: 2" in out
        assert "P3: 3" not in out
        # the cap is visible with the hidden count + a runnable decode pointer
        assert "+5 more param" in out
        assert "canair decode HVAC 2201A0" in out

    def test_hint_generic_without_ecu_pid(self, capsys):
        decoded = {f"P{i}": i for i in range(5)}
        _print_decoded_preview(decoded, limit=2)
        out = capsys.readouterr().out
        assert "+3 more param" in out
        assert "canair decode <ECU> <PID>" in out

    def test_exactly_at_limit_no_hint(self, capsys):
        _print_decoded_preview({"A": 1, "B": 2, "C": 3}, limit=3, ecu="HVAC", pid="2201A0")
        assert "more param" not in capsys.readouterr().out
