"""What ``canair memory dump`` prints around the read: the header and the summary."""

from __future__ import annotations

import io
import json
import re

import pytest

from canair.cli.commands.memory.render import (
    address_width,
    dump_header_lines,
    print_dump_header,
    print_dump_summary,
)
from canair.live.memory.dump import DumpRun
from canair.memory.store import Dump, Gap, Stretch
from canair.render.paint import Painter

PLAIN = Painter(io.StringIO())
RANGES = [(0x62C0, 0xFFBF), (0x10CC0, 0x1113F), (0x13F40, 0x620FF)]


def _dump(**kw) -> Dump:
    base = {
        "ecu": "MCU",
        "tx_id": 0x7E3,
        "memory_format": "kwp",
        "taken": "2026-11-12T17:00:00",
        "asked": RANGES,
        "stretches": [Stretch(0x62C0, bytes(0x9D00)), Stretch(0x10CC0, bytes(0x480))],
        "gaps": [Gap(0x13F40, 0x620FF, "out_of_range", 0x31)],
        "software": "8D:EAEEHEL-MS2-D000",
    }
    return Dump(**{**base, **kw})


def _strip(text: str) -> str:
    return re.sub(r"\x1b\[[0-9;]*m", "", text)


def test_addresses_take_the_width_of_the_space():
    assert address_width(0xFFBF) == 6 and address_width(0x167CBF) == 6
    assert address_width(0x1000000) == 8 and address_width(0x4000BFFE) == 8


class TestHeader:
    def test_it_names_the_ecu_the_way_the_transport_and_every_range(self):
        lines = dump_header_lines("MCU", 0x7E3, RANGES, 61, "wican-ws", PLAIN)
        assert lines[0] == "\n  MCU (0x7E3) · memory dump · block 61 · over wican-ws"
        assert [line.split() for line in lines[1:4]] == [
            ["1", "0x0062C0-0x00FFBF", "39.2", "KiB"],
            ["2", "0x010CC0-0x01113F", "1.1", "KiB"],
            ["3", "0x013F40-0x0620FF", "312.4", "KiB"],
        ]
        assert lines[4] == "  3 ranges · 352.8 KiB"

    def test_one_range_is_not_numbered(self):
        lines = dump_header_lines("VCU", 0x7E2, [(0x180000, 0x187FFF)], 64, None, PLAIN)
        assert lines[0] == "\n  VCU (0x7E2) · memory dump · block 64"
        assert lines[1].split() == ["0x180000-0x187FFF", "32.0", "KiB"]
        assert lines[2] == "  32.0 KiB"

    def test_a_long_list_is_cut_and_says_how_many_more(self):
        ranges = [(i * 0x1000, i * 0x1000 + 0xFFF) for i in range(12)]
        lines = dump_header_lines("MCU", 0x7E3, ranges, 61, None, PLAIN)
        assert len(lines) == 1 + 8 + 1 + 1
        assert lines[9].strip() == "… and 4 more"
        assert lines[10] == "  12 ranges · 48.0 KiB"

    def test_it_goes_to_stderr(self, capsys):
        print_dump_header("MCU", 0x7E3, RANGES, 61, "wican-ws")
        captured = capsys.readouterr()
        assert captured.out == "" and "MCU (0x7E3)" in captured.err


class TestSummary:
    def test_the_result_is_a_table_on_stdout_and_the_path_on_stderr(self, capsys, tmp_path):
        dump = _dump(path=tmp_path / "20261112-170000-x")
        print_dump_summary(dump, as_json=False)
        captured = capsys.readouterr()
        out = captured.out.splitlines()
        assert out[1].split()[1:] == [
            "·",
            "MCU",
            "·",
            "40.4",
            "KiB",
            "read",
            "·",
            "software",
            "8D:EAEEHEL-MS2-D000",
        ]
        assert [line.split() for line in out[2:5]] == [
            ["0x0062C0-0x00FFBF", "39.2", "KiB", "read"],
            ["0x010CC0-0x01113F", "1.1", "KiB", "read"],
            ["0x013F40-0x0620FF", "312.4", "KiB", "out_of_range", "NRC", "0x31"],
        ]
        assert "✓ Saved to" in captured.err and "\x1b" not in captured.out + captured.err

    def test_the_run_adds_its_time_speed_and_what_the_link_cost(self, capsys):
        run = DumpRun(_dump(), seconds=3120.0, retries=3, halvings=1, drops=1, rereads=0)
        print_dump_summary(run.dump, as_json=False, run=run)
        out = capsys.readouterr().out
        assert "this run: 52 min (13 B/s)" in out
        assert "link: 3 lost replies read again · 2 blocks halved" in out
        assert "stale" not in out  # nothing to say about a clean count

    def test_a_clean_run_reports_no_costs(self, capsys):
        run = DumpRun(_dump(), seconds=120.0)
        print_dump_summary(run.dump, as_json=False, run=run)
        out = capsys.readouterr().out
        assert "2 min" in out and "lost" not in out and "halved" not in out

    def test_the_table_is_coloured_by_meaning_and_the_text_is_unchanged(self, capsys, monkeypatch):
        monkeypatch.delenv("NO_COLOR", raising=False)
        print_dump_summary(_dump(), as_json=False)
        plain = capsys.readouterr().out
        monkeypatch.setenv("FORCE_COLOR", "1")
        print_dump_summary(_dump(), as_json=False)
        coloured = capsys.readouterr().out
        assert _strip(coloured) == plain
        assert "\x1b[92mread" in coloured  # a successful read is green
        assert "\x1b[93mout_of_range  NRC 0x31" in coloured  # a refusal is yellow, with its NRC

    def test_json_gains_a_run_object_and_stays_one_document(self, capsys):
        run = DumpRun(_dump(), seconds=100.0, retries=2, halvings=1)
        print_dump_summary(run.dump, as_json=True, run=run)
        doc = json.loads(capsys.readouterr().out)
        assert doc["run"] == {
            "seconds": 100.0,
            "bytes_per_second": 413.4,
            "retries": 2,
            "halvings": 1,
            "rereads": 0,
            "stopped": None,
        }

    def test_json_without_a_run_is_what_it_was(self, capsys):
        print_dump_summary(_dump(), as_json=True)
        assert "run" not in json.loads(capsys.readouterr().out)


@pytest.mark.parametrize("seconds", [0.0])
def test_a_run_with_no_time_prints_no_speed(capsys, seconds):
    run = DumpRun(_dump(), seconds=seconds)
    print_dump_summary(run.dump, as_json=False, run=run)
    assert "B/s" not in capsys.readouterr().out
