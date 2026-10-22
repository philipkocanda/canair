"""A capture session's identity survives an edit (``canlib.captures_merge`` anchors).

A session has no id: its *content* identifies it, which is what the git merge driver, the
contribute overlay and the layered read dedupe by. Every metadata editor and repair
(``--set-notes``, ``--set-transport``, ``--set-state``, ``migrate-discover``,
``split-batched``) changes that content, so without a second notion of identity an edited
session was treated as a brand-new recording — contributed *next to* upstream's original,
and read twice from a layered profile. The anchor (date, label, version and the first
capture's time/rx/pid) is what no edit touches.
"""

from __future__ import annotations

import argparse
import json
import subprocess
from pathlib import Path

import pytest

from canlib import captures_merge
from canlib import contribute as C
from canlib.commands import _contribute_gates as gates
from canlib.commands import _contribute_report as report
from canlib.commands import contribute as cmd
from canlib.profile import Profile, resolve_profile


def _session(label="drive", time="10:00:00", *, notes=None, date="2026-07-28", pid="2101"):
    s: dict = {"date": date, "label": label, "version": "1.0.0"}
    if notes is not None:
        s["notes"] = notes
    s["captures"] = [{"rx": "0x7EC", "pid": pid, "payload": "6101AA", "time": time}]
    return s


def _doc(*sessions):
    return {"sessions": list(sessions)}


class TestAnchor:
    def test_an_edit_keeps_the_anchor_but_not_the_key(self):
        a, b = _session(), _session(notes="corrected")
        assert captures_merge.session_anchor(a) == captures_merge.session_anchor(b)
        assert captures_merge.session_key(a) != captures_merge.session_key(b)

    def test_a_repair_that_rewrites_rows_keeps_the_anchor(self):
        """split-batched keeps the first row's DID and time; migrate-rx renames the key."""
        a = _session()
        split = json.loads(json.dumps(a))
        split["captures"][0]["payload"] = "6101"
        split["captures"].append(
            {"rx": "0x7EC", "pid": "2102", "payload": "61", "time": "10:00:00"}
        )
        legacy = json.loads(json.dumps(a))
        first = legacy["captures"][0]
        legacy["captures"][0] = {"ecu": first.pop("rx"), **first}
        anchor = captures_merge.session_anchor(a)
        assert captures_merge.session_anchor(split) == anchor
        assert captures_merge.session_anchor(legacy) == anchor

    def test_a_different_recording_has_a_different_anchor(self):
        assert captures_merge.session_anchor(_session()) != captures_merge.session_anchor(
            _session(time="10:00:01")
        )

    def test_a_session_without_captures_has_no_anchor(self):
        assert captures_merge.session_anchor({"date": "d", "label": "l", "captures": []}) is None

    def test_shared_anchors_are_not_trusted(self):
        untimed = [_session(time=""), _session(time="", notes="x"), _session("other", time="")]
        assert list(captures_merge.unique_anchors(untimed).values()) == [2]


class TestOverlayReplacesAnEditedSession:
    def test_an_edited_copy_replaces_upstreams_in_place(self):
        upstream = _doc(_session("first", "09:00:00"), _session(), _session("last", "11:00:00"))
        result = captures_merge.overlay(upstream, _doc(_session(notes="corrected")))
        assert result.doc is not None
        labels = [s["label"] for s in result.doc["sessions"]]
        assert labels == ["first", "drive", "last"]  # no duplicate, order kept
        assert result.doc["sessions"][1]["notes"] == "corrected"
        [r] = result.replaced
        assert (r.index, r.label, r.time, r.fields) == (1, "drive", "10:00:00", ["notes"])
        assert result.added == 0

    def test_set_transport_repro_yields_one_session(self):
        edited = {**_session(), "transport": "wican-ws"}
        doc = captures_merge.overlay_documents(_doc(_session()), _doc(edited))
        assert doc is not None and doc["sessions"] == [edited]

    def test_new_sessions_are_still_appended_and_not_reported(self):
        result = captures_merge.overlay(_doc(_session()), _doc(_session(), _session("new", "12")))
        assert result.replaced == [] and result.added == 1

    def test_an_ambiguous_anchor_falls_back_to_appending(self):
        """Untimed legacy sessions sharing an anchor are never paired by guesswork."""
        upstream = _doc(_session(time=""), _session(time="", notes="other recording"))
        result = captures_merge.overlay(upstream, _doc(_session(time="", notes="mine")))
        assert result.replaced == []
        assert result.doc is not None and len(result.doc["sessions"]) == 3


class TestMergeDriverIdenticalReplacements:
    def test_the_same_repair_on_both_sides_merges(self):
        base = _doc(_session(), _session("other", "12:00:00"))
        repaired = {**_session(), "transport": "slcan-tcp"}
        ours = _doc(repaired, _session("other", "12:00:00"), _session("ours", "13:00:00"))
        theirs = _doc(repaired, _session("other", "12:00:00"))
        merged = captures_merge.merge_sessions(base, ours, theirs)
        assert merged == [repaired, _session("other", "12:00:00"), _session("ours", "13:00:00")]
        assert merged == captures_merge.merge_sessions(base, theirs, ours)

    def test_different_edits_of_one_session_still_conflict(self):
        base = _doc(_session())
        with pytest.raises(captures_merge.MergeConflict):
            captures_merge.merge_sessions(
                base, _doc(_session(notes="a")), _doc(_session(notes="b"))
            )

    def test_an_edit_against_a_deletion_still_conflicts(self):
        with pytest.raises(captures_merge.MergeConflict):
            captures_merge.merge_sessions(_doc(_session()), _doc(_session(notes="a")), _doc())

    def test_an_edit_on_one_side_wins_over_an_untouched_other(self):
        base = _doc(_session())
        merged = captures_merge.merge_sessions(base, _doc(_session(notes="a")), base)
        assert merged == [_session(notes="a")]


# --------------------------------------------------------------------------
# Layered profile reads
# --------------------------------------------------------------------------


@pytest.fixture
def layered(tmp_path, monkeypatch):
    """A bundled base ``ev6`` and the user's overlay on top of it, made active."""
    from canlib import config
    from canlib import profile as profile_mod

    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "cfg"))
    monkeypatch.delenv("CANAIR_PROFILES_DIR", raising=False)
    monkeypatch.setattr("canlib.profile.BUNDLED_PROFILES_DIR", tmp_path / "profiles")
    config.load_config.cache_clear()
    base = tmp_path / "profiles" / "ev6"
    (base / "ecus").mkdir(parents=True)
    (base / "captures").mkdir()
    (base / "profile.yaml").write_text('car_model: "ev6"\ninit: "ATSP6;"\n')
    (base / "ecus" / "bms.yaml").write_text("BMS:\n  tx_id: 0x7E4\n")
    overlay = tmp_path / "cfg" / "canair" / "profiles" / "ev6"
    (overlay / "captures").mkdir(parents=True)
    (overlay / "profile.yaml").write_text("extends: ev6\n")
    saved = profile_mod._active
    profile_mod._active = resolve_profile("ev6")
    yield argparse.Namespace(base=base, overlay=overlay)
    profile_mod._active = saved
    config.load_config.cache_clear()


def _write(path: Path, *sessions) -> None:
    path.write_text(json.dumps(_doc(*sessions)))


class TestLayeredReadCountsAnEditedSessionOnce:
    def test_the_overlays_edited_copy_supersedes_the_base_original(self, layered):
        from canlib.capture_store import load_all_captures

        _write(layered.base / "captures" / "2026-07-28.json", _session())
        mine = layered.overlay / "captures" / "2026-07-28.json"
        _write(mine, _session(notes="corrected"))
        rows = load_all_captures()
        assert len(rows) == 1
        assert rows[0]["session_notes"] == "corrected"
        assert rows[0]["_path"] == str(mine)

    def test_an_identical_copy_still_reads_from_the_base(self, layered):
        from canlib.capture_store import load_all_captures

        theirs = layered.base / "captures" / "2026-07-28.json"
        _write(theirs, _session())
        _write(layered.overlay / "captures" / "2026-07-28.json", _session())
        [row] = load_all_captures()
        assert row["_path"] == str(theirs)

    def test_a_single_layer_with_shared_anchors_is_unchanged(self, tmp_path):
        """Anchors only arbitrate *between* layers; one store reads as it always did."""
        from canlib.capture_store import load_all_captures

        cdir = tmp_path / "captures"
        cdir.mkdir()
        _write(cdir / "2026-07-28.json", _session(), _session(notes="x"))
        assert len(load_all_captures(cdir)) == 2


class TestLayeredScopeKeysByPath:
    def test_last_session_does_not_fold_layers_together(self, layered):
        """Both layers hold ``2026-07-28.json``; keyed by name their sessions merged."""
        from canlib.capture_dates import _session_starts

        _write(layered.base / "captures" / "2026-07-28.json", _session("base", "08:00:00"))
        _write(layered.overlay / "captures" / "2026-07-28.json", _session("mine", "18:00:00"))
        starts = _session_starts(None, None, None, None)
        assert [s.strftime("%H:%M") for s in starts] == ["08:00", "18:00"]

    def test_unresolved_state_summary_counts_both_layers_sessions(self, layered):
        from canlib.capture_store import load_all_captures, unresolved_state_summary

        def multi(label, time):
            return {**_session(label, time), "vehicle_states": ["ACC", "READY"]}

        _write(layered.base / "captures" / "2026-07-28.json", multi("base", "08:00:00"))
        _write(layered.overlay / "captures" / "2026-07-28.json", multi("mine", "18:00:00"))
        assert unresolved_state_summary(load_all_captures()) == (2, 2)


# --------------------------------------------------------------------------
# Contribute
# --------------------------------------------------------------------------


def _git(repo, *args):
    subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True)


def _upstream_with(ws: Path, *sessions) -> Path:
    """A workspace repo whose committed upstream already holds ``sessions``."""
    ws.mkdir(parents=True)
    _git(ws, "init", "-b", "main")
    _git(ws, "config", "user.email", "t@example.com")
    _git(ws, "config", "user.name", "T")
    _git(ws, "config", "commit.gpgsign", "false")
    prof = ws / "profiles" / "testcar"
    (prof / "ecus").mkdir(parents=True)
    (prof / "captures").mkdir()
    (prof / "ecus" / "bms.yaml").write_text("x")
    (prof / "profile.yaml").write_text("car_model: Test EV\ninit: ATSP6;\n")
    _write(prof / "captures" / "2026-07-28.json", *sessions)
    _git(ws, "add", "-A")
    _git(ws, "commit", "-m", "seed")
    return prof / "captures" / "2026-07-28.json"


def _source(root: Path, *sessions) -> Profile:
    (root / "ecus").mkdir(parents=True)
    (root / "captures").mkdir()
    (root / "ecus" / "bms.yaml").write_text("x")
    (root / "profile.yaml").write_text("car_model: Test EV\ninit: ATSP6;\n")
    _write(root / "captures" / "2026-07-28.json", *sessions)
    return Profile("testcar", root)


def _args(**kw):
    base = {
        "captures": True,
        "branch": None,
        "title": None,
        "body": None,
        "repo_dir": None,
        "dry_run": False,
        "diff": False,
        "yes": True,
        "json": True,
    }
    base.update(kw)
    return argparse.Namespace(**base)


@pytest.fixture
def no_gh(monkeypatch):
    monkeypatch.setattr(gates, "_validate", lambda p: (True, ""))
    monkeypatch.setattr(C, "preflight", lambda: C.Preflight(gh="gh", git="git", authenticated=True))


class TestContributeAnEditedSession:
    def test_copy_profile_replaces_and_reports(self, tmp_path):
        upstream = tmp_path / "ws" / "profiles" / "testcar" / "captures"
        upstream.mkdir(parents=True)
        _write(upstream / "2026-07-28.json", _session())
        src = _source(tmp_path / "src", _session(notes="corrected"))
        replaced: list = []
        C.copy_profile(src, tmp_path / "ws", include_captures=True, replaced=replaced.append)
        sessions = json.loads((upstream / "2026-07-28.json").read_text())["sessions"]
        assert sessions == [_session(notes="corrected")]
        [r] = replaced
        assert r.file == "captures/2026-07-28.json" and r.session.fields == ["notes"]

    def test_diff_reports_the_replacement_and_adds_no_duplicate(
        self, tmp_path, monkeypatch, capsys, no_gh
    ):
        ws = tmp_path / "ws"
        upstream_file = _upstream_with(ws, _session())
        prof = _source(tmp_path / "src", _session(notes="corrected"))
        monkeypatch.setattr(cmd, "active", lambda: prof)
        assert cmd.run(_args(repo_dir=str(ws), diff=True)) == report.OK
        payload = json.loads(capsys.readouterr().out)
        [r] = payload["replaced_sessions"]
        assert r["fields"] == ["notes"] and r["label"] == "drive"
        assert len(json.loads(upstream_file.read_text())["sessions"]) == 1

    def test_a_replacement_needs_consent(self, tmp_path, monkeypatch, capsys, no_gh):
        ws = tmp_path / "ws"
        _upstream_with(ws, _session())
        prof = _source(tmp_path / "src", _session(notes="corrected"))
        monkeypatch.setattr(cmd, "active", lambda: prof)
        assert cmd.run(_args(repo_dir=str(ws), yes=False)) == report.CANNOT
        payload = json.loads(capsys.readouterr().out)
        assert "replaces 1 upstream capture session" in payload["error"]
        assert payload["replaced_sessions"][0]["file"] == "captures/2026-07-28.json"

    def test_with_yes_it_is_committed(self, tmp_path, monkeypatch, capsys, no_gh):
        ws = tmp_path / "ws"
        upstream_file = _upstream_with(ws, _session())
        prof = _source(tmp_path / "src", _session(notes="corrected"))
        monkeypatch.setattr(cmd, "active", lambda: prof)
        assert cmd.run(_args(repo_dir=str(ws), dry_run=True)) == report.OK
        assert json.loads(upstream_file.read_text())["sessions"] == [_session(notes="corrected")]

    def test_a_layered_profile_contributes_its_own_layer(self, tmp_path):
        """The captures come from the user's layer, not the read-only base it extends."""
        base = _source(tmp_path / "base", _session("base"))
        overlay = tmp_path / "overlay"
        (overlay / "captures").mkdir(parents=True)
        _write(overlay / "captures" / "2026-07-29.json", _session("mine", date="2026-07-29"))
        prof = Profile("testcar", base.root, (overlay,))
        dest = C.copy_profile(prof, tmp_path / "ws", include_captures=True)
        assert sorted(p.name for p in (dest / "captures").glob("*.json")) == ["2026-07-29.json"]


# A 3-DID batch reply and its parts, enough of each to establish their lengths.
BC01, BC02, BC03 = "62BC01400000000002", "62BC0200000000", "62BC03FDEE3C730A000000"
BATCH = "62" + BC01[2:] + BC02[2:] + BC03[2:]


def _did_session(label, time, rows):
    caps = [{"rx": "0x778", "pid": pid, "payload": p, "time": time} for pid, p in rows]
    return {"date": "2026-07-28", "label": label, "captures": caps}


class TestContributeUnsplitBatches:
    def _corpus(self):
        rows = [("22BC01", BC01), ("22BC02", BC02), ("22BC03", BC03)] * 10
        return _did_session("baseline", "08:00:00", rows)

    def test_an_added_unsplit_reply_needs_consent(self, tmp_path, monkeypatch, capsys, no_gh):
        ws = tmp_path / "ws"
        _upstream_with(ws, self._corpus())
        bad = _did_session("leaky", "09:00:00", [("22BC01", BATCH)])
        prof = _source(tmp_path / "src", self._corpus(), bad)
        monkeypatch.setattr(cmd, "active", lambda: prof)
        assert cmd.run(_args(repo_dir=str(ws), yes=False)) == report.CANNOT
        payload = json.loads(capsys.readouterr().out)
        assert "split-batched" in payload["error"]
        [u] = payload["unsplit_batches"]
        assert u["split_into"] == ["22BC01", "22BC02", "22BC03"]
        assert u["file"] == "captures/2026-07-28.json"

    def test_one_already_upstream_is_not_this_contributions_doing(
        self, tmp_path, monkeypatch, no_gh
    ):
        ws = tmp_path / "ws"
        bad = _did_session("leaky", "09:00:00", [("22BC01", BATCH)])
        _upstream_with(ws, self._corpus(), bad)
        root = tmp_path / "src"
        prof = _source(root, self._corpus(), bad)
        _write(root / "captures" / "2026-07-29.json", _session("new", date="2026-07-29"))
        assert gates.unsplit_in_contribution(prof, C.preflight(), ws) == []
        C.copy_profile(prof, ws, include_captures=True)
        assert gates.unsplit_in_contribution(prof, C.preflight(), ws) == []
