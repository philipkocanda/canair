"""Tests for ``investigate --support-map``: the capture bridge, verdicts and reporting."""

import argparse
import json
import random
from typing import ClassVar

from canlib.analysis.align import LoadedPid
from canlib.analysis.support_map import MapFit, reverse_bits
from canlib.commands.investigate import parser as inv_parser
from canlib.commands.investigate import support_map as inv_map

# Map marking bits {0,1,2,3} of each of the 4 covered bytes.
MAP = bytes([0xF0, 0xF0, 0xF0, 0xF0])


def _args(**kw):
    ns = {
        "map_max_offset": 0,
        "map_max_chance": 0.01,
        "map_allow_violations": 0,
        "map_all": False,
        "notation": None,
        "json": False,
        "state": None,
        "label": None,
    }
    return argparse.Namespace(**(ns | kw))


def _capture(ecu, pid, i, payload):
    return {
        "ecu": ecu,
        "pid": pid,
        "date": "2026-10-25",
        "time": f"10:{i // 60:02d}:{i % 60:02d}",
        "payload": payload,
    }


def _lp(pid="22BC03", n=80, seed=1, stray=0, mask=MAP, ecu="IGPM"):
    """``n`` captures of ``<SID><DID> <mask> <data>``; ``stray`` adds an unmarked bit that moves."""
    rng = random.Random(seed)
    lp = LoadedPid(ecu, pid)
    echo = ("62" + pid[2:]) if pid.startswith("22") else ("61" + pid[2:])
    for i in range(n):
        data = bytes(
            (rng.getrandbits(8) & reverse_bits(m)) | (stray * rng.getrandbits(1) << 6) for m in mask
        )
        lp.captures.append(_capture(ecu, pid, i, echo + (mask + data).hex().upper()))
    return lp


class TestScanPid:
    def test_finds_the_map_and_strips_the_header(self):
        scan = inv_map.scan_pid("IGPM", "22BC03", _lp(), {}, _args())
        assert scan.best.mask == MAP
        assert scan.best.covered_start == 4  # data offsets exclude the SID and DID echo
        assert scan.header == 3

    def test_a_service_21_pid_has_a_one_byte_header(self):
        scan = inv_map.scan_pid("HVAC", "2101", _lp("2101", ecu="HVAC"), {}, _args())
        assert scan.header == 2
        assert scan.best.mask == MAP

    def test_no_timed_captures(self):
        empty = LoadedPid("IGPM", "22BC03")
        assert inv_map.scan_pid("IGPM", "22BC03", empty, {}, _args()) is None

    def test_wican_labels_follow_the_frame_layout(self):
        scan = inv_map.scan_pid("IGPM", "22BC03", _lp(), {}, _args())
        # Data starts at payload index 7 -> WiCAN B10 in a multi-frame reply.
        assert scan.labels[7] == 10


class TestVerdicts:
    def _verdict(self, lp, **arg_overrides):
        scan = inv_map.scan_pid("IGPM", "22BC03", lp, {}, _args(**arg_overrides))
        inv_map.judge({"22BC03": scan}, _args(**arg_overrides))
        return scan

    def test_a_clean_map_is_strong(self):
        assert self._verdict(_lp()).verdict == "strong"

    def test_a_stray_moving_bit_outside_the_map_is_rejected(self):
        scan = self._verdict(_lp(stray=1))
        assert scan.verdict == "rejected"
        assert scan.best.violations

    def test_violations_can_be_tolerated(self):
        assert self._verdict(_lp(stray=1), map_allow_violations=4).verdict == "strong"

    def test_noise_is_not_a_map(self):
        rng = random.Random(8)
        lp = LoadedPid("IGPM", "22BC03")
        for i in range(60):
            noise = bytes(rng.getrandbits(8) for _ in range(8)).hex()
            lp.captures.append(_capture("IGPM", "22BC03", i, "62BC03" + noise))
        assert self._verdict(lp).verdict in ("none", "weak")

    @staticmethod
    def _scan_with(pid, fit):
        return inv_map.PidScan("IGPM", pid, 80, 3, b"", [fit], fit, {})

    @staticmethod
    def _fit(chance, key=("bit", 0, 4)):
        unit, at, length = key
        return MapFit(unit, at, length, b"\x01", 80, 4, 4, (), (), (), 6, chance, placements=8)

    def test_siblings_that_are_weak_alone_corroborate_each_other(self):
        scans = {p: self._scan_with(p, self._fit(-1.6)) for p in ("22BC04", "22BC05")}
        inv_map.judge(scans, _args())
        assert {s.verdict for s in scans.values()} == {"corroborated"}
        assert all(s.family_log10 < -2 for s in scans.values())

    def test_a_weak_fit_alone_stays_weak(self):
        scans = {"22BC04": self._scan_with("22BC04", self._fit(-1.6))}
        inv_map.judge(scans, _args())
        assert scans["22BC04"].verdict == "weak"
        assert scans["22BC04"].family_log10 == 0.0

    def test_siblings_with_a_different_placement_do_not_help(self):
        scans = {
            "22BC04": self._scan_with("22BC04", self._fit(-1.6)),
            "22BC05": self._scan_with("22BC05", self._fit(-1.6, key=("byte", 0, 4))),
        }
        inv_map.judge(scans, _args())
        assert {s.verdict for s in scans.values()} == {"weak"}


class TestReport:
    PARAMS: ClassVar[dict] = {
        "DOOR": {"expression": "B10:0", "verified": True},
        "BELT": {"expression": "B10:1", "verified": False},
        "RAW": {"expression": "B12", "verified": True},
    }

    def _scan(self, params=None, **kw):
        scan = inv_map.scan_pid("IGPM", "22BC03", _lp(**kw), params or {}, _args())
        inv_map.judge({"22BC03": scan}, _args())
        return scan

    def test_json_lists_marked_signals_with_their_definers(self):
        doc = inv_map._as_json(self._scan(self.PARAMS), _args())
        by_label = {s["label"]: s for s in doc["marked"]}
        assert by_label["B10:0"]["defined_by"] == "DOOR" and by_label["B10:0"]["verified"] is True
        assert by_label["B10:1"]["defined_by"] == "BELT" and by_label["B10:1"]["verified"] is False
        assert by_label["B10:2"]["defined_by"] is None
        assert doc["mask"] == "F0F0F0F0" and doc["unit"] == "bit"

    def test_a_whole_byte_read_does_not_define_its_bits(self):
        # Same rule as `coverage`: a raw-byte read does not account for the byte's bits.
        doc = inv_map._as_json(self._scan(self.PARAMS), _args())
        b12 = [s for s in doc["marked"] if s["label"].startswith("B12:")]
        assert b12 and all(s["defined_by"] is None for s in b12)

    def test_the_declare_command_is_proposed_for_a_strong_fit(self):
        doc = inv_map._as_json(self._scan(), _args())
        assert doc["declare"] == (
            "canair pids set-support-map IGPM 22BC03 --at 0 --length 4 --unit bit"
        )

    def test_no_declare_command_for_a_rejected_fit(self):
        doc = inv_map._as_json(self._scan(stray=1), _args())
        assert doc["verdict"] == "rejected" and doc["declare"] is None
        assert doc["violations"]

    def test_a_definition_outside_the_map_is_reported(self):
        params = {"ODD": {"expression": "B10:6", "verified": True}}
        doc = inv_map._as_json(self._scan(params), _args())
        assert doc["outside_defined"] == [
            {"signal": "ODD", "where": "B10:6", "continuation": False}
        ]

    def test_json_serialises(self):
        json.dumps(inv_map._as_json(self._scan(self.PARAMS), _args()), default=str)

    def test_text_view_names_the_map_and_the_open_work(self, capsys):
        inv_map._print_scan(self._scan(self.PARAMS), _args())
        out = capsys.readouterr().out
        assert "STRONG" in out and "F0 F0 F0 F0" in out
        assert "16 signals marked" in out
        assert "open, moving" in out
        assert "declare: canair pids set-support-map IGPM 22BC03" in out

    def test_text_view_for_a_rejected_fit_explains_the_refusal(self, capsys):
        inv_map._print_scan(self._scan(stray=1), _args())
        out = capsys.readouterr().out
        assert "REJECTED" in out and "outside the map" in out
        assert "declare:" not in out and "closest fit" in out

    def test_notation_relabels_without_changing_the_data(self):
        wican = inv_map._as_json(self._scan(), _args())
        torque = inv_map._as_json(self._scan(), _args(notation="torque"))
        assert [s["offset"] for s in wican["marked"]] == [s["offset"] for s in torque["marked"]]
        assert wican["marked"][0]["label"] != torque["marked"][0]["label"]


class TestCli:
    def _parse(self, *argv):
        top = argparse.ArgumentParser()
        inv_parser.add_parser(top.add_subparsers())
        return top.parse_args(["investigate", "uds", *argv])

    def test_flags_and_defaults(self):
        args = self._parse("IGPM", "--support-map")
        assert args.support_map is True
        assert (args.map_max_offset, args.map_max_chance, args.map_allow_violations) == (0, 0.01, 0)
        assert args.map_all is False

    def test_views_are_mutually_exclusive(self, capsys):
        args = self._parse("IGPM", "--support-map", "--counters")
        assert args.func(args) == 2
        assert "separate views" in capsys.readouterr().err

    def test_events_and_dwell_still_combine(self):
        # The exclusivity check must not break the long-standing --events --dwell pairing.
        args = self._parse("IGPM", "22BC03", "--events", "--dwell")
        assert args.events and args.dwell
        assert not (args.support_map or args.counters)
