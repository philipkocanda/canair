"""The DTC log in user state: ECU references, car scoping, open codes, totals, migration."""

from __future__ import annotations

import argparse
import json
from typing import ClassVar

import pytest
import yaml

from canlib.dtc import history
from canlib.dtc import log as dtc_log
from canlib.dtc.refs import EcuRefs
from canlib.dtc.scope import ANY_CAR, Car
from canlib.profile import Profile

# A two-ECU registry as the log sees it: rx -> name, rx -> request id.
REFS = EcuRefs(tx_by_rx={0x7EC: 0x7E4, 0x7EA: 0x7E2}, names={0x7EC: "BMS", 0x7EA: "VCU"})
BMS, VCU = "0x7EC", "0x7EA"


def _scan(scope, ecus=None, ts="t", **extra):
    entry = dtc_log.build_scan(scope, ecus or {}, timestamp=ts, unread=extra.pop("unread", None))
    return {**entry, **extra}


def _codes(*codes):
    return {"protocol": "uds", "dtcs": list(codes)}


@pytest.fixture
def logp(tmp_path, monkeypatch):
    """The active profile's DTC log, redirected to a scratch file."""
    p = tmp_path / "log.yaml"
    monkeypatch.setattr(dtc_log, "log_path", lambda path=None: p if path is None else path)
    return p


@pytest.fixture
def refs(monkeypatch):
    """Resolve logged references against :data:`REFS` instead of the active profile."""
    monkeypatch.setattr(EcuRefs, "current", classmethod(lambda cls: REFS))
    return REFS


def _write(path, *scans, clears=()):
    path.write_text(yaml.safe_dump({"format": 2, "scans": list(scans), "clears": list(clears)}))


class TestEcuRefs:
    def test_name_and_label_come_from_the_profile(self):
        assert REFS.name(BMS) == "BMS"
        assert REFS.label(BMS) == "BMS (0x7E4)"

    def test_an_address_the_profile_does_not_know_is_shown_as_is(self):
        assert REFS.name("0x799") is None
        assert REFS.label("0x799") == "0x799"


class TestCar:
    def test_variant(self):
        car = Car(variant="28kwh", several=True)
        assert car.admits({"variant": "28kwh"})
        assert not car.admits({"variant": "38kwh"})
        assert car.admits({})  # untagged reads as the selected car

    def test_no_car_named_on_a_multi_variant_profile_admits_nothing(self):
        car = Car(variant=None, several=True)
        assert car.unnamed
        assert not car.admits({"variant": "28kwh"}) and not car.admits({})
        assert ANY_CAR.admits({"variant": "28kwh"})

    def test_a_single_variant_profile_needs_no_choice(self):
        assert Car(variant=None, several=False).admits({"variant": "only"})

    def test_revision_is_checked_per_ecu(self):
        car = Car(variant="v", revisions={"VCU": "uds", "BMS": "kwp"})
        entry = {"revisions": {"VCU": "kwp"}}
        assert not car.admits(entry)
        assert not car.admits(entry, ["VCU"])
        assert car.admits(entry, ["BMS"])  # another ECU's revision is no reason to drop it
        assert car.admits({}, ["VCU"])  # an untagged entry reads as the resolved revision


class TestTagging:
    def test_entries_carry_the_car_and_the_scoped_ecus_revision(self, logp, refs, monkeypatch):
        tag = {"variant": "28kwh", "revisions": {"VCU": "kwp", "BMS": "uds"}, "attribution": {}}
        monkeypatch.setattr("canlib.variants.tags.capture_tag", lambda _p: dict(tag))
        dtc_log.append_scan(_scan("all"))
        dtc_log.append_scan(_scan(VCU))
        dtc_log.append_clear(dtc_log.build_clear(BMS, timestamp="t"))
        data = dtc_log.load_log()
        scans = data["scans"]
        assert scans[0]["variant"] == "28kwh"
        assert scans[0]["revisions"] == {"VCU": "kwp", "BMS": "uds"}
        assert scans[1]["revisions"] == {"VCU": "kwp"}
        assert data["clears"][0]["revisions"] == {"BMS": "uds"}
        assert "attribution" not in scans[0]

    def test_the_log_file_is_written_with_a_header_and_its_format(self, tmp_path):
        p = tmp_path / "sub" / "log.yaml"
        dtc_log.append_scan(_scan("all"), path=p)
        assert p.read_text().startswith("# DTC scan history")
        assert yaml.safe_load(p.read_text())["format"] == dtc_log.FORMAT

    def test_an_ecu_is_recorded_by_address_alone(self):
        clear = dtc_log.build_clear(BMS, protocol="kwp", group="0xFFFF", timestamp="t")
        assert clear == {"timestamp": "t", "rx": BMS, "protocol": "kwp", "group": "0xFFFF"}


class TestLatestMatching:
    def test_skips_another_cars_scans(self, logp, refs):
        _write(logp, _scan("all", ts="t1", variant="28kwh"), _scan("all", ts="t2", variant="38kwh"))
        car = Car(variant="28kwh", several=True)
        assert dtc_log.latest_matching("all", car=car)["timestamp"] == "t1"
        assert dtc_log.prior_matching("all", "t3", car=car)["timestamp"] == "t1"

    def test_skips_another_revision_of_the_ecu(self, logp, refs):
        _write(
            logp,
            _scan(VCU, ts="t1", revisions={"VCU": "uds"}),
            _scan(VCU, ts="t2", revisions={"VCU": "kwp"}),
        )
        car = Car(variant="v", revisions={"VCU": "uds"})
        assert dtc_log.latest_matching(VCU, car=car)["timestamp"] == "t1"

    def test_clears_between_two_timestamps(self, logp, refs):
        _write(
            logp,
            clears=[dtc_log.build_clear(BMS, timestamp=t) for t in ("t1", "t2", "t3")],
        )
        assert [c["timestamp"] for c in dtc_log.clears_between("t1", "t2", car=ANY_CAR)] == ["t2"]


class TestDiff:
    EMPTY: ClassVar = {"cleared": [], "new": [], "persisting": [], "cleared_manually": []}

    def test_an_unread_ecu_is_not_cleared(self):
        prev = _scan("all", {BMS: _codes("P1")})
        curr = _scan("all", unread=[BMS])
        assert dtc_log.diff_scans(prev, curr, refs=REFS) == self.EMPTY

    def test_another_revision_is_not_compared(self):
        prev = _scan("all", {VCU: _codes("P1")}, revisions={"VCU": "kwp"})
        curr = _scan("all", {VCU: _codes("P2")}, revisions={"VCU": "uds"})
        assert dtc_log.diff_scans(prev, curr, refs=REFS) == self.EMPTY

    def test_codes_a_clear_wiped_are_not_reported_as_cleared(self):
        prev = _scan("all", {BMS: _codes("P1", "P2"), VCU: _codes("U1")})
        curr = _scan("all", {BMS: _codes("P2")})
        diff = dtc_log.diff_scans(prev, curr, [dtc_log.build_clear(BMS)], refs=REFS)
        assert diff["cleared_manually"] == [(BMS, "P1"), (BMS, "P2")]
        assert diff["cleared"] == [(VCU, "U1")]  # gone on its own
        assert diff["new"] == [(BMS, "P2")]  # came back after the clear

    def test_the_diff_names_ecus_from_the_profile(self):
        prev = _scan("all", {BMS: _codes("P1")}, ts="t0")
        lines = "\n".join(
            dtc_log.format_diff(dtc_log.diff_scans(prev, _scan("all"), refs=REFS), prev, REFS)
        )
        assert "BMS (0x7E4) P1" in lines


class TestEcuStatus:
    def test_latest_single_ecu_scan_wins(self):
        data = {
            "scans": [
                _scan("all", {BMS: _codes("P1")}, ts="t1"),
                _scan(BMS, {BMS: _codes("P2")}, ts="t2"),
            ]
        }
        status = history.ecu_status(BMS, car=ANY_CAR, data=data, refs=REFS)
        assert status is not None and status.codes == ("P2",) and status.timestamp == "t2"
        assert status.ecu == "BMS (0x7E4)"

    def test_a_sweep_that_read_it_but_lists_nothing_means_clean(self):
        data = {"scans": [_scan("all", {BMS: _codes("P1")}, ts="t1"), _scan("all", ts="t2")]}
        status = history.ecu_status(BMS, car=ANY_CAR, data=data, refs=REFS)
        assert status is not None and status.codes == ()

    def test_a_sweep_that_could_not_read_it_says_nothing(self):
        data = {
            "scans": [
                _scan("all", {BMS: _codes("P1")}, ts="t1"),
                _scan("all", unread=[BMS], ts="t2"),
            ]
        }
        status = history.ecu_status(BMS, car=ANY_CAR, data=data, refs=REFS)
        assert status is not None and status.codes == ("P1",) and status.timestamp == "t1"

    def test_a_clear_leaves_it_clean_until_it_is_read_again(self):
        scans = [_scan(BMS, {BMS: _codes("P1")}, ts="t1")]
        clears = [dtc_log.build_clear(BMS, timestamp="t1")]  # same second as the read
        status = history.ecu_status(
            BMS, car=ANY_CAR, data={"scans": scans, "clears": clears}, refs=REFS
        )
        assert status is not None and status.codes == () and status.cleared
        scans.append(_scan(BMS, {BMS: _codes("P1")}, ts="t2"))
        status = history.ecu_status(
            BMS, car=ANY_CAR, data={"scans": scans, "clears": clears}, refs=REFS
        )
        assert status is not None and status.codes == ("P1",) and not status.cleared

    def test_another_ecus_scan_is_ignored_and_none_means_never_scanned(self):
        data = {"scans": [_scan(VCU, {VCU: _codes("P1")})]}
        assert history.ecu_status(BMS, car=ANY_CAR, data=data, refs=REFS) is None

    def test_a_revision_change_hides_the_old_codes(self):
        data = {"scans": [_scan("all", {VCU: _codes("P1")}, revisions={"VCU": "kwp"})]}
        car = Car(variant="v", revisions={"VCU": "uds"})
        assert history.ecu_status(VCU, car=car, data=data, refs=REFS) is None
        assert history.ecu_status(VCU, car=ANY_CAR, data=data, refs=REFS) is not None


class TestStats:
    def test_counts_seen_open_and_other_cars(self, logp, refs):
        _write(
            logp,
            _scan("all", {BMS: _codes("B1", "B2")}, ts="t1", variant="a"),
            _scan("all", {BMS: _codes("B1")}, ts="t2", variant="a"),
            _scan("all", {BMS: _codes("B9")}, ts="t3", variant="b"),
        )
        stats = history.log_stats(car=Car(variant="a", several=True))
        assert (stats.scans, stats.seen, stats.open_codes, stats.other_cars) == (2, 2, 1, 1)
        assert stats.last_scan == "t2"
        assert [s.codes for s in stats.open] == [("B1",)]


def _profile(tmp_path, name="car", meta="", ecus=None):
    root = tmp_path / name
    (root / "ecus").mkdir(parents=True)
    (root / "profile.yaml").write_text("car_model: x\ninit: ''\n" + meta)
    for ecu, tx in (ecus or {}).items():
        (root / "ecus" / f"{ecu.lower()}.yaml").write_text(f"{ecu}:\n  tx_id: {tx:#x}\n")
    return root


def _v1(path, scans=(), clears=()):
    path.write_text(yaml.safe_dump({"scans": list(scans), "clears": list(clears)}))


class TestLegacyMigration:
    def test_moves_and_merges_the_in_profile_log(self, tmp_path, monkeypatch, capsys):
        monkeypatch.setattr(dtc_log, "log_dir", lambda: tmp_path / "state")
        root = _profile(tmp_path)
        legacy = root / "dtc_log.yaml"
        _v1(legacy, [_scan("all", ts="t1"), _scan("all", ts="t3")])
        dest = tmp_path / "state" / "car.yaml"
        dest.parent.mkdir()
        _write(dest, _scan("all", ts="t2"), _scan("all", ts="t3"))

        assert dtc_log.log_file(Profile("car", root)) == dest
        assert not legacy.exists()
        assert [s["timestamp"] for s in dtc_log.load_log(dest)["scans"]] == ["t1", "t2", "t3"]
        assert "moved the DTC log into user state" in capsys.readouterr().err

    def test_folds_in_a_log_filed_under_an_old_profile_name(self, tmp_path, monkeypatch):
        monkeypatch.setattr(dtc_log, "log_dir", lambda: tmp_path / "state")
        root = _profile(tmp_path, "new-name", meta="aliases:\n  old-name: {}\n")
        (tmp_path / "state").mkdir()
        old = tmp_path / "state" / "old-name.yaml"
        _write(old, _scan("all", ts="t1"))
        dest = dtc_log.log_file(Profile("new-name", root))
        assert not old.exists()
        assert len(dtc_log.load_log(dest)["scans"]) == 1

    def test_a_current_log_with_nothing_to_fold_in_is_left_alone(self, tmp_path, monkeypatch):
        monkeypatch.setattr(dtc_log, "log_dir", lambda: tmp_path / "state")
        root = _profile(tmp_path)
        dest = tmp_path / "state" / "car.yaml"
        dest.parent.mkdir()
        dest.write_text("# mine\nformat: 2\nscans: []\n")
        dtc_log.log_file(Profile("car", root))
        assert dest.read_text() == "# mine\nformat: 2\nscans: []\n"


class TestFormatUpgrade:
    """Format 1 named ECUs by display label and stored derived entries."""

    V1_SCANS: ClassVar = [
        {
            "timestamp": "t1",
            "scope": "all",
            "ecus": {
                "BMS (0x7E4)": {"tx": "0x7E4", "protocol": "kwp", "dtcs": ["P1"]},
                "Unknown-7C6 (0x7C6)": {"tx": "0x7C6", "protocol": "uds", "dtcs": ["B1"]},
            },
            "unread": ["VCU (0x7E2)"],
            "variant": "28kwh",
        },
        {
            "timestamp": "t2",
            "scope": "BMS (0x7E4)",
            "ecus": {"BMS (0x7E4)": {"tx": "0x7E4", "protocol": "kwp", "dtcs": []}},
            "label": "post-clear",
        },
        {"timestamp": "t3", "scope": "OLDNAME (0x7E4)", "ecus": {}, "label": "after fix"},
    ]
    V1_CLEARS: ClassVar = [
        {
            "timestamp": "t2",
            "type": "manual",
            "scope": "BMS (0x7E4)",
            "ecu": "BMS (0x7E4)",
            "protocol": "kwp",
            "group": "0xFFFF",
            "codes": ["P1"],
        },
        {"timestamp": "t4", "type": "detected", "scope": "all", "cleared": [["BMS (0x7E4)", "P1"]]},
    ]

    def test_labels_become_addresses_and_derived_entries_go(self):
        from canlib.migrations.dtc_log import upgrade

        out = upgrade(
            {"scans": self.V1_SCANS, "clears": self.V1_CLEARS}, lambda tx: f"0x{tx + 8:03X}"
        )
        assert out["scans"] == [
            {
                "timestamp": "t1",
                "scope": "all",
                "ecus": {
                    "0x7EC": {"protocol": "kwp", "dtcs": ["P1"]},
                    "0x7CE": {"protocol": "uds", "dtcs": ["B1"]},
                },
                "unread": ["0x7EA"],
                "variant": "28kwh",
            },
            {"timestamp": "t3", "scope": "0x7EC", "ecus": {}, "label": "after fix"},
        ]
        assert out["clears"] == [
            {
                "timestamp": "t2",
                "rx": "0x7EC",
                "protocol": "kwp",
                "group": "0xFFFF",
                "codes": ["P1"],
            }
        ]

    def test_a_current_format_doc_is_untouched(self):
        from canlib.migrations.dtc_log import upgrade

        doc = {"format": 2, "scans": [_scan("BMS (0x7E4)")]}  # not a label in format 2
        assert upgrade(doc, lambda tx: "never")["scans"] == doc["scans"]

    def test_runs_on_first_use_with_the_profiles_addressing(self, tmp_path, monkeypatch, capsys):
        monkeypatch.setattr(dtc_log, "log_dir", lambda: tmp_path / "state")
        root = _profile(tmp_path, meta="addressing:\n  rx_offset: -0x20\n", ecus={"BMS": 0x7E4})
        dest = tmp_path / "state" / "car.yaml"
        dest.parent.mkdir()
        _v1(dest, self.V1_SCANS, self.V1_CLEARS)
        dtc_log.log_file(Profile("car", root))
        data = yaml.safe_load(dest.read_text())
        assert data["format"] == 2
        assert list(data["scans"][0]["ecus"]) == ["0x7C4", "0x7A6"]  # -0x20, known or not
        assert [c["rx"] for c in data["clears"]] == ["0x7C4"]
        assert "name each ECU by its response address" in capsys.readouterr().err


class TestCommands:
    def test_bare_dtc_hints_at_the_log(self, logp, refs, capsys):
        from canlib.commands import dtc as dtc_cmd

        _write(logp, _scan("all", {BMS: _codes("B1", "B2")}))
        args = argparse.Namespace(dtc=None, dtc_all=False)
        assert dtc_cmd.run(args) == 2
        out = capsys.readouterr().out
        assert "DTC log: 2 DTC(s) seen in 1 scan(s)" in out
        assert "2 open on 1 ECU(s)" in out and "`canair dtc log`" in out

    def test_bare_dtc_without_a_log_says_nothing_about_it(self, logp, capsys):
        from canlib.commands import dtc as dtc_cmd

        assert dtc_cmd.run(argparse.Namespace(dtc=None, dtc_all=False)) == 2
        assert "DTC log" not in capsys.readouterr().out

    def test_history_flag_still_works(self, logp, capsys):
        from canlib.commands import dtc as dtc_cmd

        _write(logp, _scan("all"))
        args = argparse.Namespace(dtc=None, dtc_all=False, dtc_history=True, json=False)
        assert dtc_cmd.run(args) == 0
        captured = capsys.readouterr()
        assert "DTC log: all" in captured.out
        assert "canair dtc log" in captured.err

    def test_log_json(self, logp, refs, capsys):
        from canlib.commands.dtc import log as log_cmd

        _write(
            logp,
            _scan("all", {BMS: _codes("P1")}, ts="t1"),
            _scan("all", ts="t3"),
            clears=[dtc_log.build_clear(BMS, timestamp="t2")],
        )
        assert log_cmd.run(argparse.Namespace(dtc=None, json=True)) == 0
        out = json.loads(capsys.readouterr().out)
        assert out["scan"]["timestamp"] == "t3" and out["previous"]["timestamp"] == "t1"
        assert [c["timestamp"] for c in out["clears_since"]] == ["t2"]
        assert out["diff"]["cleared_manually"] == [[BMS, "P1"]]
        assert out["log"]["scans"] == 2

    def test_kinds_are_injected(self):
        from canlib.cli import _inject_default_subcommand as inject

        assert inject(["dtc", "BMS"]) == ["dtc", "read", "BMS"]
        assert inject(["dtc", "--all"]) == ["dtc", "read", "--all"]
        assert inject(["dtc"]) == ["dtc", "read"]
        assert inject(["dtc", "log", "BMS"]) == ["dtc", "log", "BMS"]

    def test_ecu_detail_shows_open_dtcs(self, logp, capsys):
        from canlib.commands import ecu as ecu_cmd
        from canlib.dtc.refs import ecu_ref
        from canlib.ecus import resolve_tx

        tx = resolve_tx("BMS")
        assert tx is not None
        _write(logp, _scan("all", {ecu_ref(tx): _codes("P1AAA-00")}))
        assert ecu_cmd.run(argparse.Namespace(ecu="BMS", view=None, json=False)) == 0
        out = capsys.readouterr().out
        assert "1 open" in out and "P1AAA-00" in out

    def test_config_reports_dtc_logs(self, tmp_path, monkeypatch):
        from canlib.commands.config import _dtc_log_stats

        monkeypatch.setattr(dtc_log, "log_dir", lambda: tmp_path)
        monkeypatch.setattr("canlib.migrations.dtc_log.migrate_all", lambda: None)
        _write(tmp_path / "other.yaml", _scan("all", {"0x708": _codes("B1")}))
        stats = _dtc_log_stats(None)
        assert stats["dir"] == str(tmp_path)
        assert stats["logs"][0]["profile"] == "other"
        assert stats["logs"][0]["dtcs_seen"] == 1
        assert "open" not in stats["logs"][0]  # only the active car's codes are "open"


NAMES = {BMS: "BMS", VCU: "VCU"}
_BACKFILL_ECUS = {
    "VCU": {"tx_id": 0x7E2, "revisions": {"kwp": {}, "uds": {}}},
    "BMS": {"tx_id": 0x7E4},
}


class TestBackfill:
    def _variants(self, **revisions):
        from canlib.variants import Variant

        return {
            vid: Variant(id=vid, name=vid, revisions={"VCU": revs})
            for vid, revs in revisions.items()
        }

    def test_tags_only_what_the_profile_settles(self):
        from canlib.migrations.dtc_log import implied_tags

        variants = self._variants(**{"40kwh": ("kwp",), "60kwh": ("kwp", "uds")})
        assert implied_tags(
            {"scope": "all", "variant": "40kwh"}, variants, _BACKFILL_ECUS, NAMES
        ) == {"revisions": {"VCU": "kwp"}}
        assert (
            implied_tags({"scope": "all", "variant": "60kwh"}, variants, _BACKFILL_ECUS, NAMES)
            == {}
        )
        assert implied_tags({"scope": "all"}, variants, _BACKFILL_ECUS, NAMES) == {}  # which car?
        tagged = {"scope": "all", "variant": "40kwh", "revisions": {"VCU": "kwp"}}
        assert implied_tags(tagged, variants, _BACKFILL_ECUS, NAMES) == {}

    def test_a_single_ecu_scan_is_tagged_only_for_its_ecu(self):
        from canlib.migrations.dtc_log import implied_tags

        variants = self._variants(**{"40kwh": ("kwp",)})
        assert implied_tags({"scope": BMS}, variants, _BACKFILL_ECUS, NAMES) == {"variant": "40kwh"}
        assert implied_tags({"rx": VCU}, variants, _BACKFILL_ECUS, NAMES) == {
            "variant": "40kwh",
            "revisions": {"VCU": "kwp"},
        }

    def test_runs_on_first_use_of_the_log(self, tmp_path, monkeypatch, capsys):
        from pathlib import Path

        monkeypatch.setattr(dtc_log, "log_dir", lambda: tmp_path)
        root = Path(__file__).parent / "fixtures" / "profiles" / "variants"
        log = tmp_path / "variants.yaml"
        _write(log, _scan("all", ts="t1", variant="40kwh"), _scan("all", ts="t2", variant="60kwh"))
        dtc_log.log_file(Profile("variants", root))
        scans = dtc_log.load_log(log)["scans"]
        assert scans[0]["revisions"] == {"VCU": "kwp"}
        assert "revisions" not in scans[1]
        assert "tagged 1 earlier DTC-log entr" in capsys.readouterr().err


class TestMigrateAll:
    def test_moves_every_profiles_legacy_log(self, tmp_path, monkeypatch):
        from canlib.migrations.dtc_log import migrate_all

        roots = tmp_path / "profiles"
        for name in ("car-a", "car-b"):
            (roots / name / "ecus").mkdir(parents=True)
            (roots / name / "profile.yaml").write_text("car_model: x\ninit: ''\n")
            _write(roots / name / "dtc_log.yaml", _scan("all", ts=name))
        monkeypatch.setattr(dtc_log, "log_dir", lambda: tmp_path / "state")
        monkeypatch.setattr(
            "canlib.profile.selectable_profiles", lambda: {n: roots / n for n in ("car-a", "car-b")}
        )
        monkeypatch.setattr(
            "canlib.profile.resolve_profile", lambda name: Profile(name, roots / name)
        )
        migrate_all()
        for name in ("car-a", "car-b"):
            assert not (roots / name / "dtc_log.yaml").exists()
            moved = dtc_log.load_log(tmp_path / "state" / f"{name}.yaml")["scans"]
            assert [s["timestamp"] for s in moved] == [name]

    def test_update_moves_them_before_a_reinstall(self, monkeypatch):
        from canlib.commands import update

        calls = []
        monkeypatch.setattr("canlib.migrations.dtc_log.migrate_all", lambda: calls.append(1))
        monkeypatch.setattr("canlib.install_context.snapshot_profile_risks", lambda clone: [])
        update._warn_about_snapshot_data(None, None)
        assert calls == [1]
