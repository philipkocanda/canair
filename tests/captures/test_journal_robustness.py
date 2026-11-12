"""Journal and save failure modes: damaged journals, retries, live writers, concurrent saves."""

import json
import threading

import pytest

import canair.captures.journal as cj
from canair.captures import io as capture_io
from canair.captures.journal import (
    CaptureJournal,
    JournalInUseError,
    list_orphans,
    recover,
)
from canair.captures.save import save_session
from canair.cli.profile_context import active


def _session_count(captures_dir):
    return sum(
        len(capture_io.load_capture_file(f)["sessions"]) for f in captures_dir.glob("*.json")
    )


def _journal(tmp_path, *rows):
    """A journal with one payload row per ``(date, time, payload)``, as a dead writer left it."""
    j = CaptureJournal.open(tmp_path, label="L", profile=active())
    for date, time, payload in rows:
        j.append("0x7EC", "2101", payload, time, date)
    j.flush()
    return j


class TestDamagedJournal:
    def test_a_record_that_is_not_an_object_is_skipped(self, tmp_path):
        j = _journal(tmp_path, ("2026-11-01", "12:00:01", "6101AA"))
        with open(j.path, "a") as f:
            f.write("123\n[1, 2]\n")
        j._close_fh()
        recover(j.path, profile=active())
        assert _session_count(tmp_path) == 1

    def test_a_tail_cut_inside_a_multibyte_character_is_dropped(self, tmp_path):
        j = _journal(tmp_path, ("2026-11-01", "12:00:01", "6101AA"))
        with open(j.path, "ab") as f:
            f.write(b'{"type": "meta", "notes": "caf' + "é".encode()[:1])
        j._close_fh()
        recover(j.path, profile=active())
        [(session,)] = [
            capture_io.load_capture_file(f)["sessions"] for f in tmp_path.glob("*.json")
        ]
        assert [c["payload"] for c in session["captures"]] == ["6101AA"]


class TestRetriedReconcile:
    def test_a_day_saved_before_a_failure_is_not_saved_again(self, tmp_path, monkeypatch):
        j = _journal(
            tmp_path, ("2026-11-01", "23:59:59", "6101AA"), ("2026-11-02", "00:00:01", "6101BB")
        )
        j._close_fh()
        real, calls = cj.save_session, []

        def fail_on_second_day(*args, **kwargs):
            calls.append(1)
            if len(calls) == 2:
                raise OSError("disk full")
            return real(*args, **kwargs)

        monkeypatch.setattr(cj, "save_session", fail_on_second_day)
        with pytest.raises(OSError):
            recover(j.path, profile=active())
        monkeypatch.setattr(cj, "save_session", real)
        recover(j.path, profile=active())

        for day, payload in (("2026-11-01", "6101AA"), ("2026-11-02", "6101BB")):
            [session] = capture_io.load_capture_file(tmp_path / f"{day}.json")["sessions"]
            assert [c["payload"] for c in session["captures"]] == [payload]
        assert not j.path.exists()


class TestLiveJournal:
    def test_a_running_sessions_journal_is_not_an_orphan(self, tmp_path):
        live = _journal(tmp_path, ("2026-11-01", "12:00:01", "6101AA"))
        assert list_orphans(tmp_path) == []
        live._close_fh()  # the writer exits
        assert list_orphans(tmp_path) == [live.path]

    def test_recover_and_discard_refuse_a_running_sessions_journal(self, tmp_path):
        live = _journal(tmp_path, ("2026-11-01", "12:00:01", "6101AA"))
        for discard in (False, True):
            with pytest.raises(JournalInUseError):
                recover(live.path, discard=discard, profile=active())
        live.append("0x7EC", "2101", "6101BB", "12:00:02", "2026-11-01")
        live.reconcile()
        [session] = capture_io.load_capture_file(tmp_path / "2026-11-01.json")["sessions"]
        assert len(session["captures"]) == 2


class TestSaveSession:
    def test_concurrent_saves_into_one_day_all_land(self, tmp_path):
        n = 12
        journals = [
            _journal(tmp_path, ("2026-11-01", "12:00:01", f"6101{i:02X}")) for i in range(n)
        ]
        barrier = threading.Barrier(n)

        def reconcile(journal):
            barrier.wait()
            journal.reconcile()

        threads = [threading.Thread(target=reconcile, args=(j,)) for j in journals]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        assert _session_count(tmp_path) == n

    @pytest.mark.parametrize("bad", ["../../outside/x", "2026-1-1xxxx", "abcdefghij", "x/y/z/abcd"])
    def test_a_malformed_date_never_names_a_path_outside_the_store(self, tmp_path, bad):
        store = tmp_path / "captures"
        session = {"date": bad, "label": "x", "captures": [{"rx": "0x7EC", "pid": "2101"}]}
        written = save_session(session, store, profile=active())
        assert written.parent == store
        assert written.name != f"{bad[:10]}.json"
        assert {p.parent for p in tmp_path.rglob("*.json")} == {store}

    def test_other_top_level_keys_survive_a_save(self, tmp_path):
        day = tmp_path / "2026-11-01.json"
        day.write_text(json.dumps({"sessions": [], "extra": {"keep": 1}}))
        session = {"date": "2026-11-01", "label": "x", "captures": [{"rx": "0x7EC", "pid": "2101"}]}
        save_session(session, tmp_path, profile=active())
        assert json.loads(day.read_text())["extra"] == {"keep": 1}


class TestStringStates:
    def test_a_string_in_a_stored_file_is_one_token_not_its_characters(self, tmp_path):
        from canair.captures.store import flatten_session
        from canair.states.spans import span_state_union, states_at

        session = {
            "date": "2026-11-01",
            "label": "x",
            "vehicle_states": "CHARGING",
            "captures": [{"rx": "0x7EC", "pid": "2101", "payload": "6101AA", "time": "12:00:01"}],
        }
        [entry] = flatten_session(tmp_path / "2026-11-01.json", 0, session, profile=active())
        assert entry["session_states"] == ["CHARGING"]
        spans = [{"at": "12:00:00", "states": "CHARGING"}]
        assert states_at(spans, "12:00:05") == ["CHARGING"]
        assert span_state_union(spans) == {"CHARGING"}


class TestKeepChangesAcrossMidnight:
    def test_an_unchanged_pid_keeps_a_row_in_each_days_file(self, tmp_path):
        j = CaptureJournal.open(tmp_path, label="L", keep_mode="changes", profile=active())
        j.append("0x7EC", "2101", "6101AA", "23:59:58", "2026-11-01")
        j.append("0x7EC", "2101", "6101AA", "00:00:01", "2026-11-02")
        j._close_fh()
        recover(j.path, profile=active())
        for day in ("2026-11-01", "2026-11-02"):
            [session] = capture_io.load_capture_file(tmp_path / f"{day}.json")["sessions"]
            assert len(session["captures"]) == 1


class TestActuationEdges:
    def test_a_request_with_spaces_is_kept(self):
        from datetime import datetime

        from canair.captures.actuations import actuation_record, actuations_by_date

        rec = actuation_record(
            datetime(2026, 11, 1, 12, 0, 0),
            "0x7A8",
            "iocontrol",
            "BC0C",
            "on",
            request="2F BC 0C 03",
            outcome="ok",
        )
        assert rec["request"] == "2FBC0C03"
        [entry] = actuations_by_date([rec], "2026-11-01")["2026-11-01"]
        assert entry["request"] == "2FBC0C03"

    def test_a_suffixed_session_date_still_places_its_actuations(self):
        from canair.captures.actuations import actuations_in

        act = {"at": "12:00:00", "kind": "iocontrol", "id": "BC0C", "action": "on"}
        entry = {"date": "2026-04-17-b", "session_actuations": [act], "_path": "p"}
        assert len(actuations_in([entry])) == 1


class TestStateOptions:
    def test_a_repeated_and_comma_separated_state_option_is_split(self):
        from canair.states.tokens import parse_states

        assert parse_states(["acc,ready", "charging"]) == ["ACC", "READY", "CHARGING"]


class TestMalformedCaptureFiles:
    """A damaged capture file is reported by name, never as a bare traceback."""

    @pytest.mark.parametrize(
        "text",
        ["", '{"sessions": [', "\ufeff{}", "null", '{"sessions": null}', '{"sessions": [null]}'],
    )
    def test_load_all_captures_names_the_file(self, tmp_path, text):
        from canair.captures.folding import CaptureFormatError
        from canair.captures.store import load_all_captures

        (tmp_path / "2026-11-01.json").write_text(text, encoding="utf-8")
        with pytest.raises(CaptureFormatError, match=r"2026-11-01\.json"):
            load_all_captures(tmp_path, profile=active())

    @pytest.mark.parametrize("captures", ['"x"', "[null]", '["x"]', "5"])
    def test_a_session_whose_captures_are_not_objects_names_the_file(self, tmp_path, captures):
        from canair.captures.folding import CaptureFormatError
        from canair.captures.store import load_all_captures

        text = '{"sessions": [{"date": "2026-11-01", "label": "x", "captures": ' + captures + "}]}"
        (tmp_path / "2026-11-01.json").write_text(text)
        with pytest.raises(CaptureFormatError, match=r"2026-11-01\.json.*captures"):
            load_all_captures(tmp_path, profile=active())

    def test_a_null_captures_list_is_an_empty_session(self, tmp_path):
        from canair.captures.store import load_all_captures

        text = '{"sessions": [{"date": "2026-11-01", "label": "x", "captures": null}]}'
        (tmp_path / "2026-11-01.json").write_text(text)
        assert load_all_captures(tmp_path, profile=active()) == []

    def test_validate_reports_an_unreadable_file_instead_of_crashing(self, tmp_path):
        from canair.validation.capture_schema import CaptureSchemaChecker
        from canair.validation.captures import validate_captures_file

        path = tmp_path / "2026-11-01.json"
        path.write_text('{"sessions": [')
        [error] = validate_captures_file(path, CaptureSchemaChecker({}), set())
        assert error.startswith("unreadable: 2026-11-01.json")
