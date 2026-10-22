"""Tests for multi-DID replies stored whole under one DID (``canlib.unsplit_batches``).

A batch reply starts ``62 D1`` like a single reply to ``D1``, so one that leaked into a per-DID
read was saved as ``D1``'s payload with every other DID's data appended.
"""

from __future__ import annotations

import json

import pytest

from canlib import capture_io, profile
from canlib.commands.captures.maint import cmd_split_batched
from canlib.commands.validate import _run_captures
from canlib.commands.validate.unsplit import unsplit_batch_warnings
from canlib.misfiled import PayloadSample
from canlib.pids import clear_cache
from canlib.unsplit_batches import find_unsplit_batches, split_unsplit_batches

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
        samples = _corpus((IGPM, "22BC01", BC01 + "AAAAAA", 4), (IGPM, "22BC01", BATCH, 1))
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
