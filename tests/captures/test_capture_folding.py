"""Tests for the lossless on-disk folding of repeated captures."""

from __future__ import annotations

import json
import random
from typing import ClassVar

import pytest

from canlib.captures import io as capture_io
from canlib.captures.folding import (
    INLINE_FIELD,
    MERGED_FIELD,
    CaptureFormatError,
    _Folder,
    expand_captures,
    fold_captures,
)


def cap(pid: str, payload: str, time: str | None, rx: str = "0x7EC", **extra) -> dict:
    row: dict = {"rx": rx, "pid": pid, "payload": payload}
    if time is not None:
        row["time"] = time
    row.update(extra)
    return row


def raw_fold(rows: list[dict]) -> list:
    """The folder's own output, without :func:`fold_captures`'s round-trip fallback."""
    folder = _Folder()
    for row in rows:
        folder.add(row)
    return folder.result()


def assert_lossless(rows: list[dict]) -> list:
    """Assert the folder round-trips on its own (not merely via the fallback)."""
    folded = raw_fold(rows)
    assert expand_captures(folded) == rows
    assert fold_captures(rows) == (folded if len(folded) < len(rows) else rows)
    return folded


class TestFold:
    def test_interleaved_repeats_fold_by_time(self):
        rows = [
            cap("2101", "AA", "10:00:00.000"),
            cap("2102", "BB", "10:00:00.100"),
            cap("2101", "AA", "10:00:05.000"),
            cap("2102", "BC", "10:00:05.100"),
            cap("2101", "AA", "10:00:10.000"),
        ]
        folded = assert_lossless(rows)
        assert folded[0][MERGED_FIELD] == [5000, 5000]
        assert [r["payload"] for r in folded] == ["AA", "BB", "BC"]

    def test_grouped_repeats_fold_inline(self):
        rows = [
            cap("2101", "AA", "10:00:00.000"),
            cap("2101", "AA", "10:00:05.000"),
            cap("2101", "AA", "10:00:10.000"),
            cap("2102", "BB", "10:00:00.100"),
            cap("2102", "BB", "10:00:05.100"),
        ]
        folded = assert_lossless(rows)
        assert folded == [
            {**rows[0], INLINE_FIELD: [5000, 5000]},
            {**rows[3], INLINE_FIELD: [5000]},
        ]

    def test_a_change_breaks_the_run(self):
        rows = [
            cap("2101", "AA", "10:00:00.000"),
            cap("2101", "AB", "10:00:05.000"),
            cap("2101", "AA", "10:00:10.000"),
        ]
        assert fold_captures(rows) == rows

    def test_time_going_backwards_survives(self):
        rows = [
            cap("2101", "AA", "10:00:00.000"),
            cap("2102", "BB", "10:00:01.000"),
            cap("2101", "AA", "10:00:05.000"),
            cap("2102", "BB", "09:00:00.000"),  # a reconnect / merged journal
            cap("2101", "AA", "09:00:04.000"),
            cap("2101", "AA", "10:00:10.000"),
        ]
        assert_lossless(rows)

    def test_equal_timestamps_survive(self):
        rows = [
            cap("2101", "A1", "10:00:00.000"),
            cap("2102", "BB", "10:00:00.000"),
            cap("2101", "A2", "10:00:05.000"),
            cap("2102", "BB", "10:00:05.000"),
            cap("2101", "A3", "10:00:10.000"),
            cap("2102", "BB", "10:00:10.000"),
        ]
        assert_lossless(rows)

    def test_second_precision_folds_and_mixed_precision_does_not(self):
        rows = [cap("2101", "AA", "10:00:00"), cap("2101", "AA", "10:00:05")]
        assert assert_lossless(rows)[0][INLINE_FIELD] == [5000]
        mixed = [cap("2101", "AA", "10:00:00"), cap("2101", "AA", "10:00:05.000")]
        assert fold_captures(mixed) == mixed

    @pytest.mark.parametrize(
        "second",
        [
            cap("2101", "AA", None),
            cap("2101", "AA", "10:00:05.5"),
            cap("2101", "AA", "10:00:05.000", elapsed_ms=12),
            cap("2101", "AA", "10:00:05.000", notes="n"),
            {"pid": "2101", "rx": "0x7EC", "payload": "AA", "time": "10:00:05.000"},
            cap("2101", "AA", "10:00:05.000", rx="0x7ED"),
        ],
        ids=["untimed", "odd-time", "elapsed", "note", "field-order", "other-ecu"],
    )
    def test_only_an_identical_row_folds(self, second):
        rows = [cap("2101", "AA", "10:00:00.000"), second]
        assert fold_captures(rows) == rows

    def test_untimed_rows_between_runs_survive(self):
        rows = [
            cap("2101", "AA", "10:00:00.000"),
            cap("2102", "BB", None),
            cap("2101", "AA", "10:00:05.000"),
            cap("2101", "AA", "10:00:10.000"),
        ]
        assert_lossless(rows)

    def test_non_capture_rows_are_left_alone(self):
        rows = [cap("2101", "AA", "10:00:00.000"), "junk", cap("2101", "AA", "10:00:01.000")]
        assert fold_captures(rows) == rows

    def test_refuses_rows_already_carrying_a_gap_list(self):
        with pytest.raises(CaptureFormatError):
            fold_captures([{**cap("2101", "AA", "10:00:00.000"), MERGED_FIELD: [1]}])

    @pytest.mark.parametrize("seed", range(300))
    def test_random_sessions_round_trip(self, seed):
        rnd = random.Random(seed)
        pids = [f"21{i:02d}" for i in range(rnd.randint(1, 5))]
        ms = rnd.randint(0, 3_600_000)
        rows = []
        for _ in range(rnd.randint(0, 60)):
            ms = max(0, min(86_399_999, ms + rnd.choice([0, 0, 1, 250, 1000, -5000])))
            t = f"{ms // 3_600_000:02d}:{ms // 60_000 % 60:02d}:{ms // 1000 % 60:02d}"
            if rnd.random() < 0.9:
                t += f".{ms % 1000:03d}"
            row = cap(rnd.choice(pids), rnd.choice(["AA", "AA", "AA", "BB"]), t)
            if rnd.random() < 0.05:
                row.pop("time")
            rows.append(row)
        if rnd.random() < 0.5:  # a session stored grouped by PID
            rows.sort(key=lambda r: r["pid"])
        assert_lossless(rows)


class TestExpand:
    def test_rows_without_gaps_pass_through(self):
        rows = [cap("2101", "AA", "10:00:00.000")]
        assert expand_captures(rows) == rows

    @pytest.mark.parametrize(
        "row",
        [
            {**cap("2101", "AA", "10:00:00.000"), MERGED_FIELD: "5"},
            {**cap("2101", "AA", "10:00:00.000"), MERGED_FIELD: [1.5]},
            {**cap("2101", "AA", "10:00:00.000"), MERGED_FIELD: [True]},
            {**cap("2101", "AA", None), INLINE_FIELD: [5]},
            {**cap("2101", "AA", "23:59:59.000"), INLINE_FIELD: [2000]},
            {**cap("2101", "AA", "10:00:00"), INLINE_FIELD: [1500]},
            {**cap("2101", "AA", "10:00:00.000"), INLINE_FIELD: [1], MERGED_FIELD: [1]},
        ],
        ids=["not-a-list", "float", "bool", "untimed", "past-midnight", "precision", "both"],
    )
    def test_malformed_gaps_raise(self, row):
        with pytest.raises(CaptureFormatError):
            expand_captures([row])


class TestCaptureText:
    DOC: ClassVar[dict] = {
        "sessions": [
            {
                "date": "2026-10-25",
                "label": "L",
                "vehicle_states": ["PARKED"],
                "quality": {"exchanges": 3},
                "captures": [
                    cap("2101", "AA", "10:00:00.000"),
                    cap("2102", "BB", "10:00:00.100"),
                    cap("2101", "AA", "10:00:05.000"),
                ],
            },
            {"date": "2026-10-25-2", "label": "empty", "captures": []},
        ]
    }

    def test_one_capture_per_line_and_metadata_pretty(self):
        lines = capture_io.capture_text(self.DOC).splitlines()
        assert (
            '        {"rx":"0x7EC","pid":"2101","payload":"AA","time":"10:00:00.000",'
            in (lines[lines.index('      "captures": [') + 1])
        )
        assert '      "vehicle_states": [' in lines
        assert '      "captures": []' in lines

    def test_identical_to_json_dumps_outside_captures(self):
        meta = {
            "sessions": [
                {k: v for k, v in s.items() if k != "captures"} for s in self.DOC["sessions"]
            ]
        }
        assert (
            capture_io.capture_text(meta) == json.dumps(meta, indent=2, ensure_ascii=False) + "\n"
        )

    def test_folds_on_disk_and_expands_on_load(self, tmp_path):
        path = tmp_path / "2026-10-25.json"
        capture_io.dump_capture_file(path, self.DOC)
        on_disk = json.loads(path.read_text())
        assert on_disk["sessions"][0]["captures"][0][MERGED_FIELD] == [5000]
        assert capture_io.load_capture_file(path) == self.DOC

    def test_load_names_the_file_on_a_bad_encoding(self, tmp_path):
        path = tmp_path / "2026-10-25.json"
        bad = {"sessions": [{"date": "d", "label": "", "captures": [{MERGED_FIELD: "x"}]}]}
        path.write_text(json.dumps(bad))
        with pytest.raises(CaptureFormatError, match=r"2026-10-25\.json"):
            capture_io.load_capture_file(path)
