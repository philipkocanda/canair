"""`canair coverage`: which captured reply sets a PID's length, and what is reported.

Regression cover for the review of the representative-payload selection: same-date
ordering, misfiled replies, short/variable-length replies, padding detection, NRC-only
PIDs, and the scope plumbing of ``run``.
"""

import argparse
import json
from datetime import datetime

import pytest

from canlib.commands import coverage

ECU, PID = "HVAC", "220102"
KEY = (ECU, PID)
CLEAN = "620102" + "11" * 13  # 16 bytes
PADDED = CLEAN + "00" * 4  # 20 bytes: one whole 3-frame response, possibly padded


def row(payload, *, date="2026-08-09", time="", idx=0, file=None, path=None, pid=PID, **extra):
    """One flattened capture-store row (see ``capture_store.flatten_session``)."""
    name = file or f"{date}.json"
    return {
        "file": name,
        "_path": path or f"/captures/{name}",
        "_session_idx": idx,
        "date": date,
        "time": time,
        "session_label": "x",
        "session_version": extra.pop("version", "1.18.0"),
        "transport": extra.pop("transport", "slcan-tcp"),
        "ecu": ECU,
        "pid": pid,
        "payload": payload,
        **extra,
    }


def pick(rows, **kwargs):
    return coverage.select_captures(rows, **kwargs)


class TestNewestSessionOrdering:
    def test_same_date_tie_is_broken_by_capture_time_not_file_order(self):
        """An overlay-appended session can sit after a later one in the same file."""
        rows = [
            row(CLEAN, time="15:00:00", idx=0),
            row(PADDED, time="09:00:00", idx=1),
        ]
        assert len(pick(rows).payloads[KEY]["payload"]) // 2 == 16
        assert len(pick(rows[::-1]).payloads[KEY]["payload"]) // 2 == 16

    def test_session_start_is_its_earliest_capture(self):
        """A session is placed by its first capture, whatever PID that was."""
        rows = [
            row(CLEAN, time="10:00:00", idx=0),
            row(PADDED, time="12:00:00", idx=1),
            row("620103AA", time="08:00:00", idx=1, pid="220103"),
        ]
        assert len(pick(rows).payloads[KEY]["payload"]) // 2 == 16

    def test_undated_session_sorts_oldest(self):
        rows = [row(CLEAN, date="2026-04-01"), row(PADDED, date="", file="undated.json")]
        got = pick(rows).payloads[KEY]
        assert got["date"] == "2026-04-01"
        assert pick(rows[::-1]).payloads[KEY]["date"] == "2026-04-01"

    def test_undated_sessions_alone_are_deterministic(self):
        rows = [
            row(CLEAN, date="", file="a.json"),
            row(PADDED, date="", file="b.json"),
        ]
        assert pick(rows).payloads[KEY] == pick(rows[::-1]).payloads[KEY]

    def test_layered_base_and_overlay_with_the_same_filename_stay_distinct(self):
        """Base and overlay both hold ``2026-08-09.json``; they are different sessions."""
        rows = [
            row(PADDED, time="09:00:00", path="/base/captures/2026-08-09.json"),
            row(CLEAN, time="18:00:00", path="/overlay/captures/2026-08-09.json"),
        ]
        assert len(pick(rows).payloads[KEY]["payload"]) // 2 == 16
        assert len(pick(rows[::-1]).payloads[KEY]["payload"]) // 2 == 16

    def test_session_length_tie_prefers_the_longer_length(self):
        from collections import Counter

        assert coverage.session_length(Counter({16: 2, 20: 2})) == 20
        rows = [row(CLEAN), row(PADDED)]
        assert len(pick(rows).payloads[KEY]["payload"]) // 2 == 20


class TestMisfiledReplies:
    def test_reply_echoing_another_did_is_skipped(self):
        rows = [
            row(CLEAN, date="2026-08-08"),
            row("620199" + "22" * 20, date="2026-08-09"),
        ]
        got = pick(rows).payloads[KEY]
        assert got["date"] == "2026-08-08"

    def test_kwp_reply_to_another_local_id_is_skipped(self):
        """``21xx`` answers ``61xx``: a ``6101`` reply under ``2102`` is misfiled."""
        rows = [
            row("6102" + "11" * 10, date="2026-08-08", pid="2102"),
            row("6101" + "11" * 30, date="2026-08-09", pid="2102"),
        ]
        got = pick(rows).payloads[(ECU, "2102")]
        assert got["date"] == "2026-08-08"

    def test_hk_identity_offset_is_tolerated_only_with_the_quirk(self):
        rows = [row("62F187" + "41" * 10, pid="22F188")]
        assert (ECU, "22F188") not in pick(rows).payloads
        assert (ECU, "22F188") in pick(rows, hk_f1xx_offset=True).payloads


class TestPastEnd:
    def test_signal_beyond_a_short_payload_is_reported(self):
        got = coverage.analyze_pid({"X": {"expression": "B20"}}, "620102" + "11" * 5, 2)
        assert got["past_end"] == [{"signal": "X", "bytes": [20]}]

    def test_signal_within_the_payload_is_not(self):
        got = coverage.analyze_pid({"X": {"expression": "B10"}}, CLEAN, 2)
        assert got["past_end"] == []

    def test_range_reports_only_the_bytes_past_the_end(self):
        # 8-byte payload -> WiCAN B0..B10 (FF PCI x2, 6 data, CF PCI, 2 data).
        got = coverage.analyze_pid({"W": {"expression": "[B09:B12]"}}, "620102" + "11" * 5, 2)
        assert got["frame_len"] == 11
        assert got["past_end"] == [{"signal": "W", "bytes": [11, 12]}]

    def test_variable_length_pid_takes_its_longest_reply(self):
        rows = [row(CLEAN + "33" * 4 + "42", date="2026-08-08"), row(CLEAN, date="2026-08-09")]
        assert len(pick(rows).payloads[KEY]["payload"]) // 2 == 16
        got = pick(rows, variable_length={KEY}).payloads[KEY]
        assert len(got["payload"]) // 2 == 21
        assert got["date"] == "2026-08-08"

    def test_variable_length_prefers_a_reply_that_cannot_be_padded(self):
        rows = [row(PADDED, date="2026-04-01", version=""), row(CLEAN, date="2026-08-09")]
        got = pick(rows, variable_length={KEY}).payloads[KEY]
        assert len(got["payload"]) // 2 == 16

    def test_variable_length_falls_back_to_a_possibly_padded_reply(self):
        rows = [row(PADDED, date="2026-04-01", version="")]
        got = pick(rows, variable_length={KEY}).payloads[KEY]
        assert len(got["payload"]) // 2 == 20
        assert got["may_include_padding"] is True


class TestMayBePadded:
    def test_every_payload_must_end_in_the_same_fill_byte(self):
        a, b = CLEAN + "11" * 3 + "AA", CLEAN + "11" * 3 + "00"
        assert coverage.may_be_padded([a, a], None) is True
        assert coverage.may_be_padded([a, b], None) is False
        assert coverage.may_be_padded([a, CLEAN + "11" * 3 + "42"], None) is False

    def test_lowercase_hex(self):
        assert coverage.may_be_padded([(CLEAN + "00" * 4).lower()], None) is True

    def test_selection_checks_every_payload_of_the_winning_length(self):
        rows = [
            row(PADDED, version=""),
            row(CLEAN + "11" * 3 + "42", version=""),
            row(PADDED, version=""),
            row(CLEAN + "11" * 3 + "42", version=""),
        ]
        assert pick(rows).payloads[KEY]["may_include_padding"] is False

    def test_versioned_session_is_trusted(self):
        assert coverage.may_be_padded([PADDED], "1.18.0") is False

    def test_imported_session_may_be_padded_despite_its_version(self):
        """``import uds`` stamps a version, but a pasted reading may keep its padding."""
        assert coverage.may_be_padded([PADDED], "1.18.0", "import") is True
        rows = [row(PADDED, transport="import")]
        assert pick(rows).payloads[KEY]["may_include_padding"] is True


class TestNegativeResponseOnly:
    def test_nrc_only_pid_is_recorded_with_its_code(self):
        rows = [row("7F2231", date="2026-08-08"), row("7F2222", date="2026-08-09")]
        got = pick(rows)
        assert KEY not in got.payloads
        assert got.negative_only[KEY] == {
            "nrc": "0x22",
            "name": "conditionsNotCorrect",
            "date": "2026-08-09",
            "file": "2026-08-09.json",
            "count": 2,
        }

    def test_a_positive_reply_wins(self):
        got = pick([row("7F2231", date="2026-08-09"), row(CLEAN, date="2026-08-08")])
        assert KEY in got.payloads and KEY not in got.negative_only

    def test_nrc_for_another_service_is_not_this_pids_answer(self):
        assert KEY not in pick([row("7F2111")]).negative_only


@pytest.fixture
def run_coverage(monkeypatch, capsys):
    """Run ``coverage`` end to end over synthetic rows and one synthetic PID."""

    def _run(argv, rows, parameters=None):
        params = parameters or {"SIG": {"expression": "B4", "verified": True}}
        monkeypatch.setattr("canlib.capture_store.load_all_captures", lambda *a, **k: rows)
        # `--last-session` resolves its cutoff from the same rows.
        monkeypatch.setattr("canlib.capture_dates.load_all_captures", lambda *a, **k: rows)
        monkeypatch.setattr(coverage, "load_pids", lambda *_: {})
        monkeypatch.setattr(
            coverage, "build_ecu_index", lambda *_: {ECU: {"pids": {PID: {"parameters": params}}}}
        )
        monkeypatch.setattr("canlib.ecus.canonical_ecu_name_safe", lambda n: n)
        p = coverage.add_parser(argparse.ArgumentParser().add_subparsers())
        rc = coverage.run(p.parse_args(argv))
        captured = capsys.readouterr()
        return rc, captured.out, captured.err

    return _run


class TestRun:
    def test_padding_flag_text_and_json(self, run_coverage):
        rows = [row(PADDED, version="")]
        _rc, out, _ = run_coverage([ECU, PID, "--all"], rows)
        assert "may include trailing frame padding" in out
        _rc, out, _ = run_coverage([ECU, PID, "--all", "--json"], rows)
        assert json.loads(out)[0]["capture"]["may_include_padding"] is True

    def test_past_end_text_and_json(self, run_coverage):
        rows = [row("620102" + "11" * 5)]
        params = {"SIG": {"expression": "B05", "verified": True}, "X": {"expression": "B20"}}
        rc, out, _ = run_coverage([ECU, PID], rows, params)
        assert rc == 0
        assert "PAST END X (B20)" in out and "payload ends at B10" in out
        _rc, out, _ = run_coverage([ECU, PID, "--json"], rows, params)
        (entry,) = json.loads(out)
        assert entry["past_end"] == [{"signal": "X", "bytes": [20]}]

    def test_negative_response_only_text_and_json(self, run_coverage):
        rows = [row("7F2231")]
        _rc, out, _ = run_coverage([ECU, PID], rows)
        assert "NEGATIVE RESPONSE ONLY" in out and "0x31 requestOutOfRange" in out
        assert "NO CAPTURE" not in out
        _rc, out, _ = run_coverage([ECU, PID, "--json"], rows)
        (entry,) = json.loads(out)
        assert entry["negative_response_only"] is True
        assert entry["negative_response"]["nrc"] == "0x31"
        assert "scope" not in entry
        _rc, out, _ = run_coverage([ECU, PID, "--no-capture"], rows)
        assert "NEGATIVE RESPONSE ONLY" in out

    def test_scoped_absence_says_in_scope(self, run_coverage):
        rows = [row(CLEAN, date="2026-08-09")]
        _rc, out, _ = run_coverage([ECU, PID, "--until", "2026-08-01"], rows)
        assert "NO CAPTURE in scope" in out
        assert "without captures in scope" in out
        _rc, out, _ = run_coverage([ECU, PID, "--until", "2026-08-01", "--json"], rows)
        assert json.loads(out)[0]["scope"] == ["--until"]
        _rc, out, _ = run_coverage([ECU, PID, "--all"], [])
        assert "NO CAPTURE" in out and "in scope" not in out

    def test_conflicting_scope_flags_exit_2(self, run_coverage):
        rc, _out, err = run_coverage(
            [ECU, PID, "--date", "2026-08-09", "--since", "2026-08-01"], []
        )
        assert rc == 2
        assert "--date cannot be combined" in err

    def test_scope_flags_reach_the_loader(self, monkeypatch, run_coverage):
        seen = {}

        def spy(since, until, state=None, label=None, **kwargs):
            seen.update(since=since, until=until, state=state, label=label)
            return coverage.CoverageCaptures()

        monkeypatch.setattr(coverage, "load_coverage_captures", spy)
        rows = [
            row(CLEAN, date="2026-08-08", time="10:00:00", vehicle_states=["READY"]),
            row(CLEAN, date="2026-08-09", time="11:30:00", vehicle_states=["READY"]),
        ]
        argv = [ECU, PID, "--state", "READY", "--label", "x", "--last-session"]
        assert run_coverage(argv, rows)[0] == 0
        assert seen["state"] == ["READY"]
        assert seen["label"] == "x"
        assert seen["since"] == datetime(2026, 8, 9, 11, 30)

    def test_variable_length_flag_is_read_from_the_profile(self):
        data = {"ecus": {"hvac": {"pids": {"220102": {"variable_length": True}, "2101": {}}}}}
        assert coverage.variable_length_pids(data) == frozenset({KEY})
