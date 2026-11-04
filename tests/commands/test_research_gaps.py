"""Tests for `canair research --gaps` (canlib.commands.research.gaps)."""

from types import SimpleNamespace

from canlib.commands import research
from canlib.commands.research.gaps import (
    capture_read_ranges,
    did_ranges_in_text,
    find_gaps,
    profile_read_ranges,
)


def test_ranges_in_scan_log_and_research_text():
    assert did_ranges_in_text("0101-010F,B001-B010") == [(0x0101, 0x010F), (0xB001, 0xB010)]
    assert did_ranges_in_text("0000-00FF, 0200-02FF,\n F000-F0FF") == [
        (0x0000, 0x00FF),
        (0x0200, 0x02FF),
        (0xF000, 0xF0FF),
    ]
    assert did_ranges_in_text("22BE01-22BE03") == [(0xBE01, 0xBE03)]
    assert did_ranges_in_text("2FF00002") == []  # an 8-hex request is not a DID


def test_profile_ranges_split_read_from_planned():
    ecu = {
        "scan_log": [
            {"service": 0x22, "range": "B001-B010"},
            {"service": 0x2F, "range": "B400-B4FF"},  # an IOControl sweep reads nothing
        ],
        "research": [
            {"type": "scan", "target": "22B401-22B408", "status": "pending"},
            {"type": "scan", "target": "22 C001-C010", "status": "done"},
            {"type": "iocontrol_scan", "target": "2F F000-F0FF", "status": "done"},
        ],
    }
    read, planned = profile_read_ranges(ecu)
    assert read == [(0xB001, 0xB010), (0xC001, 0xC010)]
    assert planned == [(0xB401, 0xB408)]


def test_saved_scans_count_only_when_the_ecu_answered():
    caps = [
        {"ecu": "SKM", "pid": "scan 22 B100-B110", "scan_results": {}},  # slept through it
        {
            "ecu": "PSM",
            "pid": "scan 22 B000-B01F,B400-B41F",
            "scan_results": {"rejected": "64 DIDs returned 64 NRC"},
        },
        {"ecu": "VESS", "pid": "22F011", "payload": "62F01100"},
    ]
    got = capture_read_ranges(caps)
    assert "SKM" not in got
    assert got["PSM"] == [(0xB000, 0xB01F), (0xB400, 0xB41F)]
    assert got["VESS"] == [(0xF011, 0xF011)]


def test_find_gaps_names_the_family_and_the_command():
    ecus = {
        "PSM": {"iocontrol": {"B401": {}, "B408": {}}, "scan_log": []},
        "IGPM": {"pids": {"22BC03": {}}, "iocontrol": {"BC01": {}}},
    }
    (gap,) = find_gaps(ecus)
    assert gap["ecu"] == "PSM"
    assert gap["family"] == "B4xx"
    assert gap["iocontrol_span"] == "B401-B408"
    assert gap["command"] == "canair scan range PSM --service read-did --range B400-B4FF --save"
    assert find_gaps(ecus, {"PSM": [(0xB401, 0xB401)]}) == []
    assert find_gaps(ecus, ecu="IGPM") == []


def test_gaps_refuses_research_entry_filters(capsys):
    args = SimpleNamespace(
        gaps=True,
        rtype="scan",
        status=None,
        priority=None,
        state=None,
        summary=False,
        all=False,
        ecu=None,
        json=False,
        dir=None,
    )
    assert research.run(args) == 2
    assert "--type" in capsys.readouterr().err
