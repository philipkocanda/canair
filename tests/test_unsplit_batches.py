"""Tests for multi-DID replies stored whole under one DID (``canlib.uds.unsplit_batches``).

A batch reply starts ``62 D1`` like a single reply to ``D1``, so one that leaked into a per-DID
read was saved as ``D1``'s payload with every other DID's data appended.
"""

from __future__ import annotations

import json

import pytest

from canlib import capture_io, profile
from canlib.analysis.misfiled import PayloadSample
from canlib.commands.captures.maint import cmd_split_batched
from canlib.commands.validate import _run_captures
from canlib.commands.validate.unsplit import unsplit_batch_warnings
from canlib.pids import clear_cache
from canlib.uds.unsplit_batches import find_unsplit_batches, split_unsplit_batches

IGPM, BCM = 0x778, 0x7A8
BC01 = "62BC01400000000002"
BC02 = "62BC0200000000"
BC03 = "62BC03FDEE3C730A000000"
BATCH = "62" + BC01[2:] + BC02[2:] + BC03[2:]


def _samples(*groups: tuple[int, str, str, int]) -> list[PayloadSample]:
    out: list[PayloadSample] = []
    for rx, pid, payload, count in groups:
        for _ in range(count):
            out.append(PayloadSample(len(out), rx, pid, bytes.fromhex(payload)))
    return out


def _corpus(*extra: tuple[int, str, str, int]) -> list[PayloadSample]:
    return _samples(
        (IGPM, "22BC01", BC01, 10), (IGPM, "22BC02", BC02, 10), (IGPM, "22BC03", BC03, 10), *extra
    )


class TestFind:
    def test_a_whole_batch_under_its_first_did_is_found(self):
        samples = _corpus((IGPM, "22BC01", BATCH, 1))
        [u] = find_unsplit_batches(samples)
        assert u.sample.handle == len(samples) - 1
        assert u.pids == ["22BC01", "22BC02", "22BC03"]
        assert [p.hex().upper() for _, p in u.parts] == [BC01, BC02, BC03]

    def test_padded_legacy_replies_neither_split_the_vote_nor_get_flagged(self):
        # 9 + 4 = 13 bytes: padding fills the second CAN frame (6 + 7 bytes), as stored replies do.
        samples = _corpus((IGPM, "22BC01", BC01 + "AAAAAAAA", 4), (IGPM, "22BC01", BATCH, 1))
        assert [u.sample.pid for u in find_unsplit_batches(samples)] == ["22BC01"]

    def test_a_thinly_sampled_did_anchors_nothing(self):
        samples = _samples(
            (IGPM, "22BC01", BC01, 3), (IGPM, "22BC02", BC02, 10), (IGPM, "22BC01", BATCH, 1)
        )
        assert find_unsplit_batches(samples) == []

    def test_another_ecus_dids_do_not_chain(self):
        samples = _samples(
            (IGPM, "22BC01", BC01, 10),
            (BCM, "22BC02", BC02, 10),
            (BCM, "22BC03", BC03, 10),
            (IGPM, "22BC01", BATCH, 1),
        )
        assert find_unsplit_batches(samples) == []

    def test_a_longer_reply_that_does_not_walk_to_the_end_is_left_alone(self):
        samples = _corpus((IGPM, "22BC01", BATCH + "12", 1))
        assert find_unsplit_batches(samples) == []

    @pytest.mark.parametrize(("batches", "found"), [(2, 2), (3, 3), (12, 12)])
    def test_leaked_batches_do_not_stop_their_did_s_length_being_established(self, batches, found):
        # 20 good rows: 2 batches is under the 10% the old vote tolerated, 3 just over it, and
        # 12 nearly 40% — none may hide the batches by keeping BC01's length unestablished.
        samples = _samples(
            (IGPM, "22BC01", BC01, 20),
            (IGPM, "22BC02", BC02, 10),
            (IGPM, "22BC03", BC03, 10),
            (IGPM, "22BC01", BATCH, batches),
        )
        assert len(find_unsplit_batches(samples)) == found

    def test_batches_in_the_majority_do_not_win_the_vote(self):
        samples = _samples(
            (IGPM, "22BC01", BC01, 5),
            (IGPM, "22BC02", BC02, 10),
            (IGPM, "22BC03", BC03, 10),
            (IGPM, "22BC01", BATCH, 30),
        )
        assert len(find_unsplit_batches(samples)) == 30

    def test_too_few_good_rows_still_anchor_nothing_however_many_batches_agree(self):
        samples = _samples(
            (IGPM, "22BC01", BC01, 4),
            (IGPM, "22BC02", BC02, 10),
            (IGPM, "22BC03", BC03, 10),
            (IGPM, "22BC01", BATCH, 30),
        )
        assert find_unsplit_batches(samples) == []

    def test_a_padded_majority_agrees_with_the_unpadded_length(self):
        samples = _samples(
            (IGPM, "22BC01", BC01 + "AAAAAAAA", 6),
            (IGPM, "22BC01", BC01, 4),
            (IGPM, "22BC02", BC02, 10),
            (IGPM, "22BC03", BC03, 10),
            (IGPM, "22BC01", BATCH, 1),
        )
        [u] = find_unsplit_batches(samples)
        assert [p.hex().upper() for _, p in u.parts] == [BC01, BC02, BC03]

    def test_one_truncated_reply_does_not_make_the_full_ones_look_padded(self):
        # Real IGPM BC05: one reply lost its last byte, and the full replies' data ends in 00.
        # Read as "10 bytes plus 00 padding", the full ones would agree with the short one; but
        # padding only fills out a frame, and 11 bytes does not end on one.
        bc05 = "62BC057F11200000000000"
        samples = _corpus(
            (IGPM, "22BC05", bc05[:-2], 1),
            (IGPM, "22BC05", bc05, 30),
            (IGPM, "22BC05", bc05 + "AAAA", 3),
            (IGPM, "22BC01", "62" + BC01[2:] + bc05[2:], 1),
        )
        [u] = find_unsplit_batches(samples)
        assert [p.hex().upper() for _, p in u.parts] == [BC01, bc05]

    def test_fill_that_does_not_end_on_a_frame_boundary_is_not_padding(self):
        # 12 bytes: a padded 9-byte reply would be 13. So these are 12-byte replies, and with
        # BC01 at 9 and 12 bytes in equal measure no length is established.
        samples = _corpus((IGPM, "22BC01", BC01 + "000000", 10), (IGPM, "22BC01", BATCH, 1))
        assert find_unsplit_batches(samples) == []

    def test_a_genuinely_variable_length_did_anchors_nothing(self):
        samples = _samples(
            (IGPM, "22BC01", BC01, 6),
            (IGPM, "22BC01", BC01 + "1234", 4),
            (IGPM, "22BC02", BC02, 10),
            (IGPM, "22BC03", BC03, 10),
            (IGPM, "22BC01", BATCH, 1),
        )
        assert find_unsplit_batches(samples) == []

    def test_a_reply_echoing_another_did_neither_votes_nor_is_flagged(self):
        # Rows stored as 22BC01 that echo BC02 (a misfile) would otherwise outvote BC01.
        samples = _samples(
            (IGPM, "22BC01", BC01, 5),
            (IGPM, "22BC01", BC02, 5),
            (IGPM, "22BC02", BC02, 10),
            (IGPM, "22BC03", BC03, 10),
            (IGPM, "22BC01", BATCH, 1),
            (IGPM, "22BC01", "62" + BC02[2:] + BC03[2:], 1),
        )
        assert [u.sample.handle for u in find_unsplit_batches(samples)] == [30]

    def test_trailing_fill_after_a_batch_is_still_a_batch(self):
        samples = _corpus((IGPM, "22BC01", BATCH + "AAAAAA", 1))
        [u] = find_unsplit_batches(samples)
        assert u.pids == ["22BC01", "22BC02", "22BC03"]

    def test_the_same_did_on_another_rx_is_judged_by_its_own_length(self):
        # BCM's BC01 is longer; the IGPM batch length must not leak into it, nor back.
        samples = _corpus(
            (BCM, "22BC01", BC01 + "0102", 10),
            (BCM, "22BC01", BATCH, 1),
            (IGPM, "22BC01", BATCH, 1),
        )
        assert [u.sample.rx for u in find_unsplit_batches(samples)] == [IGPM]


def _write(directory, caps: list[dict], name: str = "2026-08-08.json"):
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / name
    path.write_text(
        json.dumps({"sessions": [{"date": "2026-08-08", "label": "t", "captures": caps}]})
    )
    return path


def _caps(blob: dict | None = None) -> list[dict]:
    caps = [
        {
            "rx": "0x778",
            "pid": pid,
            "payload": payload,
            "time": f"10:{i // 60:02d}:{i % 60:02d}.000",
        }
        for i, (pid, payload) in enumerate(
            [("22BC01", BC01), ("22BC02", BC02), ("22BC03", BC03)] * 12
        )
    ]
    if blob is not None:
        caps.insert(3, blob)
    return caps


BLOB = {
    "rx": "0x778",
    "pid": "22BC01",
    "payload": BATCH,
    "time": "10:07:16.000",
    "notes": "kept",
    "elapsed_ms": 412,
}


class TestSplit:
    def test_replaces_the_row_with_one_row_per_did_at_the_same_time(self, tmp_path):
        path = _write(tmp_path, _caps(dict(BLOB)))
        report = split_unsplit_batches([tmp_path], tmp_path)
        assert [s.pids for s in report.split] == [("22BC01", "22BC02", "22BC03")]
        caps = capture_io.load_capture_file(path)["sessions"][0]["captures"]
        rows = caps[3:6]
        assert [(r["pid"], r["payload"]) for r in rows] == [
            ("22BC01", BC01),
            ("22BC02", BC02),
            ("22BC03", BC03),
        ]
        assert all(r["time"] == "10:07:16.000" and r["notes"] == "kept" for r in rows)
        assert all("elapsed_ms" not in r for r in rows)  # one round trip answered them all
        assert len(caps) == 36 + 3

    def test_is_idempotent(self, tmp_path):
        path = _write(tmp_path, _caps(dict(BLOB)))
        split_unsplit_batches([tmp_path], tmp_path)
        before = path.read_text()
        assert split_unsplit_batches([tmp_path], tmp_path).split == []
        assert path.read_text() == before

    def test_dry_run_writes_nothing(self, tmp_path):
        path = _write(tmp_path, _caps(dict(BLOB)))
        before = path.read_text()
        assert len(split_unsplit_batches([tmp_path], tmp_path, dry_run=True).split) == 1
        assert path.read_text() == before

    def test_a_read_only_layer_is_reported_not_rewritten(self, tmp_path):
        base, overlay = tmp_path / "base", tmp_path / "overlay"
        path = _write(base, _caps(dict(BLOB)))
        overlay.mkdir()
        before = path.read_text()
        report = split_unsplit_batches([base, overlay], overlay)
        assert report.split == [] and len(report.read_only) == 1
        assert path.read_text() == before

    def test_a_session_the_base_also_holds_is_left_identical_to_it(self, tmp_path):
        # A contributed-then-pulled session sits in both layers and is read once. Splitting only
        # the overlay copy would make them two sessions, every reading counted twice.
        base, overlay = tmp_path / "base", tmp_path / "overlay"
        _write(base, _caps(dict(BLOB)))
        path = _write(overlay, _caps(dict(BLOB)))
        before = path.read_text()
        report = split_unsplit_batches([base, overlay], overlay)
        assert report.split == []
        assert len(report.read_only) == 1 and len(report.shadowed) == 1
        assert report.shadowed[0].where.path == path
        assert path.read_text() == before

    def test_an_overlay_session_of_its_own_is_still_split(self, tmp_path):
        base, overlay = tmp_path / "base", tmp_path / "overlay"
        _write(base, _caps())
        _write(overlay, _caps(dict(BLOB)))
        report = split_unsplit_batches([base, overlay], overlay)
        assert len(report.split) == 1 and report.shadowed == [] and report.read_only == []

    def test_several_batches_in_one_file_are_all_split_in_place(self, tmp_path):
        caps = _caps()
        blob2 = dict(BLOB, time="10:07:30.000")
        caps.insert(9, blob2)
        caps.insert(3, dict(BLOB))
        tmp_path.mkdir(exist_ok=True)
        path = tmp_path / "2026-08-08.json"
        path.write_text(
            json.dumps(
                {
                    "sessions": [
                        {"date": "2026-08-08", "label": "a", "captures": caps},
                        {"date": "2026-08-08", "label": "b", "captures": _caps(dict(BLOB))},
                    ]
                }
            )
        )
        report = split_unsplit_batches([tmp_path], tmp_path)
        assert [(s.where.session, s.where.capture) for s in report.split] == [
            (0, 3),
            (0, 10),
            (1, 3),
        ]
        sessions = capture_io.load_capture_file(path)["sessions"]
        first = sessions[0]["captures"]
        assert len(first) == 36 + 2 * 3
        # Each blob became three rows exactly where it stood.
        assert [r["pid"] for r in first[3:6]] == ["22BC01", "22BC02", "22BC03"]
        assert [r["time"] for r in first[12:15]] == ["10:07:30.000"] * 3
        assert len(sessions[1]["captures"]) == 36 + 3
        assert split_unsplit_batches([tmp_path], tmp_path).split == []

    def test_read_dirs_that_are_not_directories_are_skipped(self, tmp_path):
        path = _write(tmp_path / "caps", _caps(dict(BLOB)))
        missing = tmp_path / "nope"
        a_file = tmp_path / "file.json"
        a_file.write_text("{}")
        report = split_unsplit_batches([missing, a_file, path.parent], path.parent)
        assert len(report.split) == 1


def _session_file(directory, keep_mode: str | None, caps: list[dict]):
    directory.mkdir(parents=True, exist_ok=True)
    session: dict = {"date": "2026-08-08", "label": "t", "captures": caps}
    if keep_mode:
        session["keep_mode"] = keep_mode
    path = directory / "2026-08-08.json"
    path.write_text(json.dumps({"sessions": [session]}))
    return path


BC02_B = "62BC0211111111"


class TestSplitKeepsTheSessionsDedupPromise:
    """A ``changes`` session stores a row only when the value changes; a split must too."""

    def _caps(self, bc02_after: str) -> list[dict]:
        caps = _caps()  # BC02 is BC02 throughout
        blob = dict(BLOB)
        caps.insert(3, blob)  # carries BC01 = BC01, BC02 = BC02, BC03 = BC03: all repeats
        caps.insert(4, {"rx": "0x778", "pid": "22BC02", "payload": bc02_after, "time": "10:07:17"})
        return caps

    def test_changes_session_drops_parts_that_repeat_the_previous_row(self, tmp_path):
        path = _session_file(tmp_path, "changes", self._caps(BC02_B))
        [s] = split_unsplit_batches([tmp_path], tmp_path).split
        assert s.unchanged == ("22BC01", "22BC02", "22BC03")
        caps = capture_io.load_capture_file(path)["sessions"][0]["captures"]
        assert len(caps) == 36 + 1  # the blob is gone, nothing new written in its place
        assert caps[3]["payload"] == BC02_B

    def test_changes_session_keeps_a_part_whose_value_changed(self, tmp_path):
        caps = _caps()
        caps.insert(3, dict(BLOB, payload="62" + BC01[2:] + BC02_B[2:] + BC03[2:]))
        path = _session_file(tmp_path, "changes", caps)
        [s] = split_unsplit_batches([tmp_path], tmp_path).split
        assert s.unchanged == ("22BC01", "22BC03")
        rows = capture_io.load_capture_file(path)["sessions"][0]["captures"]
        assert (rows[3]["pid"], rows[3]["payload"]) == ("22BC02", BC02_B)
        assert len(rows) == 36 + 1

    def test_the_first_row_of_a_pid_in_a_session_is_always_kept(self, tmp_path):
        caps = [dict(BLOB), *_caps()]
        _session_file(tmp_path, "changes", caps)
        [s] = split_unsplit_batches([tmp_path], tmp_path).split
        assert s.unchanged == ()

    def test_unique_session_drops_any_value_seen_before(self, tmp_path):
        caps = _caps()
        caps.insert(4, {"rx": "0x778", "pid": "22BC02", "payload": BC02_B, "time": "10:07:15"})
        caps.insert(5, dict(BLOB))  # BC02 = BC02 again: not the last value, but not new
        _session_file(tmp_path, "unique", caps)
        [s] = split_unsplit_batches([tmp_path], tmp_path).split
        assert "22BC02" in s.unchanged

    def test_a_session_without_dedup_keeps_every_part(self, tmp_path):
        path = _session_file(tmp_path, None, self._caps(BC02_B))
        [s] = split_unsplit_batches([tmp_path], tmp_path).split
        assert s.unchanged == ()
        assert len(capture_io.load_capture_file(path)["sessions"][0]["captures"]) == 37 + 3

    def test_dry_run_reports_unchanged_parts_without_writing(self, tmp_path):
        path = _session_file(tmp_path, "changes", self._caps(BC02_B))
        before = path.read_text()
        [s] = split_unsplit_batches([tmp_path], tmp_path, dry_run=True).split
        assert s.unchanged == ("22BC01", "22BC02", "22BC03")
        assert path.read_text() == before


def test_validate_warning_names_the_dids_and_the_repair(tmp_path):
    path = _write(tmp_path, _caps(dict(BLOB)))
    [warning] = unsplit_batch_warnings([path], {IGPM: "IGPM"})[path]
    assert warning.location == "sessions[0].captures[3] (IGPM 22BC01 @ 10:07:16.000)"
    assert "3-DID reply (22BC01 + 22BC02 + 22BC03) stored whole as 22BC01" in warning.message
    assert "canair captures split-batched" in warning.message


@pytest.fixture
def _temp_profile(tmp_path):
    saved = profile._active
    clear_cache()
    root = tmp_path / "prof"
    (root / "ecus").mkdir(parents=True)
    (root / "profile.yaml").write_text('car_model: "T"\ninit: "ATSP6;"\n')
    (root / "ecus" / "igpm.yaml").write_text(
        "IGPM:\n  tx_id: 0x770\n  identity:\n    id_protocol: UDS\n"
    )
    _write(root / "captures", _caps(dict(BLOB)))
    profile.set_active(str(root))
    clear_cache()
    yield root
    profile._active = saved
    clear_cache()


def test_validate_then_repair_end_to_end(_temp_profile, capsys):
    """A soft warning in `validate captures`; `captures split-batched` clears it."""
    assert _run_captures() == 0
    assert "stored whole as 22BC01" in capsys.readouterr().out

    assert cmd_split_batched(None, dry_run=True) == 0
    assert "would split 2026-08-08.json [0].3 IGPM 22BC01" in capsys.readouterr().out
    assert cmd_split_batched(None) == 0
    assert "Split 1 capture(s)." in capsys.readouterr().out

    assert _run_captures() == 0
    assert "stored whole" not in capsys.readouterr().out


class TestSplitBatchedCommand:
    def test_json_lists_each_split_with_its_full_path(self, _temp_profile, capsys):
        assert cmd_split_batched(None, dry_run=True, as_json=True) == 0
        out = json.loads(capsys.readouterr().out)
        [row] = out["split"]
        assert row["file"] == str(_temp_profile / "captures" / "2026-08-08.json")
        assert row["ecu"] == "IGPM" and row["split_into"] == ["22BC01", "22BC02", "22BC03"]
        assert out["read_only"] == [] and out["shadowed"] == [] and out["dry_run"] is True

    def test_nothing_to_split_says_so_and_succeeds(self, _temp_profile, tmp_path, capsys):
        clean = tmp_path / "clean"
        _write(clean, _caps())
        assert cmd_split_batched(clean) == 0
        assert "No multi-DID replies stored under a single DID" in capsys.readouterr().out

    def test_a_missing_dir_is_an_error(self, _temp_profile, tmp_path, capsys):
        assert cmd_split_batched(tmp_path / "nope") == 2
        assert "no such captures directory" in capsys.readouterr().err
        assert cmd_split_batched(tmp_path / "nope", as_json=True) == 2
        assert "no such captures directory" in json.loads(capsys.readouterr().out)["error"]

    def test_a_read_only_finding_exits_1_and_names_the_base_file(self, tmp_path, monkeypatch):
        base, overlay = tmp_path / "base", tmp_path / "overlay"
        base_file = _write(base, _caps(dict(BLOB)))
        overlay.mkdir()
        monkeypatch.setattr(
            "canlib.capture_io.resolve_capture_layers", lambda d=None: [base, overlay]
        )
        monkeypatch.setattr("canlib.ecus.build_rx_index", lambda: {IGPM: "IGPM"})
        import io
        from contextlib import redirect_stdout

        buf = io.StringIO()
        with redirect_stdout(buf):
            assert cmd_split_batched(overlay, as_json=True) == 1
        [row] = json.loads(buf.getvalue())["read_only"]
        assert row["file"] == str(base_file)


def test_validate_loads_the_corpus_once_for_both_cross_file_checks(
    _temp_profile, capsys, monkeypatch
):
    """One row can be both misfiled and an unsplit batch; both warn, from one corpus load."""
    from canlib import payload_corpus
    from canlib.commands.validate import misfiled, unsplit

    # BCM answers 22BC01 with 25 bytes, which is exactly the IGPM batch's length: the batch
    # row is an unsplit IGPM batch and, by length, looks like BCM's reply filed under IGPM.
    (_temp_profile / "ecus" / "bcm.yaml").write_text(
        "BCM:\n  tx_id: 0x7A0\n  identity:\n    id_protocol: UDS\n"
    )
    clear_cache()
    bcm = "62BC01" + bytes(range(1, 23)).hex().upper()
    assert len(bytes.fromhex(bcm)) == len(bytes.fromhex(BATCH))
    caps = _caps(dict(BLOB)) + [
        {"rx": "0x7A8", "pid": "22BC01", "payload": bcm, "time": f"11:00:0{i}.000"}
        for i in range(6)
    ]
    _write(_temp_profile / "captures", caps)

    loads: list[int] = []
    real = payload_corpus.load_payload_corpus

    def counting(files):
        loads.append(1)
        return real(files)

    def unexpected(files):
        raise AssertionError("a cross-file check loaded its own corpus")

    monkeypatch.setattr(payload_corpus, "load_payload_corpus", counting)
    monkeypatch.setattr(misfiled, "load_payload_corpus", unexpected)
    monkeypatch.setattr(unsplit, "load_payload_corpus", unexpected)
    assert _run_captures() == 0
    out = capsys.readouterr().out
    assert len(loads) == 1
    at = "sessions[0].captures[3] (IGPM 22BC01 @ 10:07:16.000)"
    assert out.count(at) == 2
    assert "stored whole as 22BC01" in out
    assert "probably BCM's reply misfiled under IGPM" in out


def test_a_capture_file_that_does_not_parse_is_left_to_the_schema_check(tmp_path):
    from canlib.payload_corpus import load_payload_corpus

    good = _write(tmp_path, _caps(dict(BLOB)))
    bad = tmp_path / "2026-08-09.json"
    bad.write_text("not json\n")
    corpus = load_payload_corpus([bad, good])
    assert {w.path for w in corpus.where} == {good}
    assert len(split_unsplit_batches([tmp_path], tmp_path, dry_run=True).split) == 1
