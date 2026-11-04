"""Tests for ``investigate --support-map``: the capture bridge, verdicts and reporting."""

import argparse
import json
import random
from typing import ClassVar

from canlib.analysis.align import LoadedPid
from canlib.analysis.support_map import MapFit, reverse_bits
from canlib.commands.investigate import parser as inv_parser
from canlib.commands.investigate import support_map as inv_map
from canlib.commands.investigate import support_map_report as inv_report

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
        doc = inv_report.as_json(self._scan(self.PARAMS), _args())
        by_label = {s["label"]: s for s in doc["marked"]}
        assert by_label["B10:0"]["defined_by"] == "DOOR" and by_label["B10:0"]["verified"] is True
        assert by_label["B10:1"]["defined_by"] == "BELT" and by_label["B10:1"]["verified"] is False
        assert by_label["B10:2"]["defined_by"] is None
        assert doc["mask"] == "F0F0F0F0" and doc["unit"] == "bit"

    def test_a_whole_byte_read_does_not_define_its_bits(self):
        # Same rule as `coverage`: a raw-byte read does not account for the byte's bits.
        doc = inv_report.as_json(self._scan(self.PARAMS), _args())
        b12 = [s for s in doc["marked"] if s["label"].startswith("B12:")]
        assert b12 and all(s["defined_by"] is None for s in b12)

    def test_the_declare_command_is_proposed_for_a_strong_fit(self):
        doc = inv_report.as_json(self._scan(), _args())
        assert doc["declare"] == (
            "canair pids set-support-map IGPM 22BC03 --at 0 --length 4 --unit bit"
        )

    def test_no_declare_command_for_a_rejected_fit(self):
        doc = inv_report.as_json(self._scan(stray=1), _args())
        assert doc["verdict"] == "rejected" and doc["declare"] is None
        assert doc["violations"]

    def test_a_definition_outside_the_map_is_reported(self):
        params = {"ODD": {"expression": "B10:6", "verified": True}}
        doc = inv_report.as_json(self._scan(params), _args())
        assert doc["outside_defined"] == [
            {"signal": "ODD", "where": "B10:6", "continuation": False}
        ]

    def test_json_serialises(self):
        json.dumps(inv_report.as_json(self._scan(self.PARAMS), _args()), default=str)

    def test_text_view_names_the_map_and_the_open_work(self, capsys):
        inv_report.print_scan(self._scan(self.PARAMS), _args())
        out = capsys.readouterr().out
        assert "STRONG" in out and "F0 F0 F0 F0" in out
        assert "16 signals marked" in out
        assert "open, moving" in out
        assert "declare: canair pids set-support-map IGPM 22BC03" in out

    def test_text_view_for_a_rejected_fit_explains_the_refusal(self, capsys):
        inv_report.print_scan(self._scan(stray=1), _args())
        out = capsys.readouterr().out
        assert "REJECTED" in out and "outside the map" in out
        assert "declare:" not in out and "closest fit" in out

    def test_notation_relabels_without_changing_the_data(self):
        wican = inv_report.as_json(self._scan(), _args())
        torque = inv_report.as_json(self._scan(), _args(notation="torque"))
        assert [s["offset"] for s in wican["marked"]] == [s["offset"] for s in torque["marked"]]
        assert wican["marked"][0]["label"] != torque["marked"][0]["label"]


class TestAbsence:
    @staticmethod
    def _noise_scan():
        rng = random.Random(8)
        lp = LoadedPid("HVAC", "2201A0")
        for i in range(60):
            noise = bytes(rng.getrandbits(8) for _ in range(8)).hex()
            lp.captures.append(_capture("HVAC", "2201A0", i, "6201A0" + noise))
        scan = inv_map.scan_pid("HVAC", "2201A0", lp, {}, _args())
        inv_map.judge({"2201A0": scan}, _args())
        return scan

    def test_varying_leading_bytes_say_the_data_starts_there(self, capsys):
        scan = self._noise_scan()
        assert scan.best is None and scan.absence == "varies"
        inv_report.print_scan(scan, _args())
        assert "no map: leading bytes vary" in capsys.readouterr().out

    def test_json_carries_the_reason(self):
        doc = inv_report.as_json(self._noise_scan(), _args())
        assert doc["absence"] == "varies" and "data starts" in doc["absence_text"]

    def test_a_found_map_has_no_absence(self):
        assert inv_map.scan_pid("IGPM", "22BC03", _lp(), {}, _args()).absence is None

    def test_the_sweep_tally_counts_each_reason(self):
        few = inv_map.scan_pid("IGPM", "22BC03", _lp(n=5), {}, _args())
        scans = [self._noise_scan(), self._noise_scan(), few]
        assert inv_report.absence_tally(scans) == (
            "2 data right after the DID echo, 1 too few captures"
        )


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


def _page(lp_pid, mask: bytes, layout, n=60, seed=3):
    """A service-21 page: ``mask`` + one value per ``(width, live)`` parameter."""
    rng = random.Random(seed)
    lp = LoadedPid("VCU", lp_pid)
    for i in range(n):
        data = bytearray(mask)
        for width, live in layout:
            data += bytes(rng.getrandbits(8) if live else 0 for _ in range(width))
        lp.captures.append(_capture("VCU", lp_pid, i, ("61" + lp_pid[2:]) + data.hex().upper()))
    return lp


# Ioniq VCU 2101's shape: FF E0 marks 11 parameters; the first is undefined, five are 2 bytes.
VCU = [(1, False), (1, True), (1, True), (2, True), (2, True), (2, True), (2, True), (2, True),
       (1, True), (1, True), (1, True)]  # fmt: skip


def _vcu_params(lp, layout=VCU, skip_first=True):
    """One signal per parameter, written in WiCAN indices for the page's frame layout."""
    from canlib.uds.byteindex import payload_index_to_wican

    sample = bytes.fromhex(lp.captures[0]["payload"])
    to_w = payload_index_to_wican(list(sample))
    params, off = {}, 2 + 4
    for k, (width, _live) in enumerate(layout):
        refs = [f"B{to_w[off + j]}" for j in range(width)]
        if not (skip_first and k == 0):
            params[f"P{k + 1}"] = {"expression": "+".join(refs)}
        off += width
    return params


class TestFieldMaps:
    def _scan(self, lp, params):
        scan = inv_map.scan_pid("VCU", lp.pid, lp, params, _args())
        inv_map.judge({lp.pid: scan}, _args())
        return scan

    def test_a_service_21_page_splits_exactly(self):
        lp = _page("2101", bytes.fromhex("FFE00000"), VCU)
        scan = self._scan(lp, _vcu_params(lp))
        assert scan.verdict == "exact"
        assert scan.field_fit.field_counts == (11, 11)

    def test_json_names_the_open_parameter_and_the_declaration(self):
        lp = _page("2101", bytes.fromhex("FFE00000"), VCU)
        doc = inv_report.as_json(self._scan(lp, _vcu_params(lp)), _args())
        assert doc["field"]["open"] == ["#1 B9"]
        assert doc["field"]["declare"] == (
            "canair pids set-support-map VCU 2101 --at 0 --length 4 --unit field"
        )
        assert "absence" not in doc

    def test_the_text_view_leads_with_the_field_reading(self, capsys):
        lp = _page("2101", bytes.fromhex("FFE00000"), VCU)
        inv_report.print_scan(self._scan(lp, _vcu_params(lp)), _args())
        out = capsys.readouterr().out
        assert "EXACT" in out and "per-field map FF E0 00 00" in out
        assert "11 parameters marked" in out and "#1 B9" in out

    def test_lsb_first_is_reported_where_msb_first_is_broken(self):
        layout = [(1, True)] * 10
        lp = _page("2101", bytes.fromhex("000003FF"), layout)
        scan = self._scan(lp, _vcu_params(lp, layout, skip_first=False))
        assert scan.field_fit.order == "lsb" and scan.verdict == "exact"
        assert "--order lsb" in inv_report.declare_command(scan)

    def test_a_strong_per_bit_fit_keeps_its_headline(self):
        scan = inv_map.scan_pid("IGPM", "22BC03", _lp(), {}, _args())
        inv_map.judge({"22BC03": scan}, _args())
        assert scan.verdict == "strong"
        assert scan.field_fit is None  # four data bytes: the bitmapped layout

    def test_a_split_resting_on_nothing_is_thin(self):
        from canlib.commands.investigate.support_fields import thin

        lp = _page("2101", bytes.fromhex("FFE00000"), VCU)
        assert thin(self._scan(lp, {}).field_fit)


class TestVariants:
    def _two_cars(self):
        lp = _page("2101", bytes.fromhex("E0000000"), [(1, True), (1, True), (3, True)], n=40)
        other = _page(
            "2101", bytes.fromhex("60000000"), [(1, False), (1, True), (3, True)], n=60, seed=5
        )
        for i, c in enumerate(other.captures):
            c["time"] = f"11:{i // 60:02d}:{i % 60:02d}"
        lp.captures += other.captures
        return lp

    def test_each_map_is_fitted_on_its_own_rows(self):
        scan = inv_map.scan_pid("VCU", "2101", self._two_cars(), {}, _args())
        inv_map.judge({"2101": scan}, _args())
        assert scan.verdict == "variants"
        assert sorted(v.lead.hex().upper() for v in scan.variants) == ["60000000", "E0000000"]
        assert sorted(v.n_rows for v in scan.variants) == [40, 60]

    def test_the_diff_names_the_bit_one_car_lacks(self, capsys):
        scan = inv_map.scan_pid("VCU", "2101", self._two_cars(), {}, _args())
        inv_map.judge({"2101": scan}, _args())
        assert inv_report.variant_diff(scan) == [1]
        inv_report.print_scan(scan, _args())
        out = capsys.readouterr().out
        assert "VARIANTS" in out and "#1" in out and "[60000000]" in out
