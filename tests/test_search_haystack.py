"""Tests for ``canair search``'s row loaders (:mod:`canlib.commands.search.haystack`) and the
command paths around them: query errors, ``--defs`` failures, layered profiles, routing.
"""

from __future__ import annotations

import argparse
import json

import pytest

from canlib.cli import _inject_default_subcommand, build_parser
from canlib.commands.search.haystack import (
    FIELD_SCAN_NOTES,
    _Tally,
    identity_rows,
    payload_rows,
    text_rows,
)
from tests.test_search import _cap, _payload, _run, _write


def _entry(**kw) -> dict:
    return {"file": "2026-07-24.json", "_path": "/a/2026-07-24.json", "_session_idx": 0, **kw}


class TestTally:
    def test_missing_dates_never_shadow_a_real_one(self):
        tally = _Tally()
        tally.add("k", "")
        tally.add("k", "2026-07-25")
        tally.add("k", "")
        tally.add("k", "2026-07-24")
        ((key, weight, first, last),) = tally.items()
        assert (key, weight, first, last) == ("k", 4, "2026-07-24", "2026-07-25")

    def test_an_undated_key_stays_undated(self):
        tally = _Tally()
        tally.add("k", "", weight=3)
        assert list(tally.items()) == [("k", 3, "", "")]


class TestTextRows:
    def test_capture_level_fields_are_rows(self):
        rows = text_rows(
            [_entry(ecu="BCM", pid="22F188", label="bench", notes="hot", response="NRC 0x31")]
        )
        assert {(r.field, r.ecu, r.text) for r in rows} == {
            ("label", "BCM", "bench"),
            ("notes", "BCM", "hot"),
            ("response", "BCM", "NRC 0x31"),
        }

    def test_scan_level_notes_and_rejected_are_text(self):
        scan = {
            "notes": "swept at idle",
            "rejected": "12 DIDs returned 12 NRC",
            "responding": [
                {"did": "01", "notes": "Raw: 6101AABB"},  # bytes: not text
                {"tx": "0x7E0", "notes": "Raw: serviceNotSupported"},  # an NRC: text
            ],
        }
        rows = text_rows([_entry(ecu="BMS", pid="scan 21 01-FF", scan_results=scan)])
        notes = {r.text for r in rows if r.field == FIELD_SCAN_NOTES}
        assert notes == {
            "swept at idle",
            "12 DIDs returned 12 NRC",
            "Raw: serviceNotSupported",
        }

    def test_same_named_files_in_two_layers_are_two_sessions(self):
        # A layered profile's base and overlay can both hold 2026-07-24.json; keying
        # the session on the bare file name skipped the overlay's session text.
        base = _entry(session_label="upstream", _path="/base/2026-07-24.json")
        mine = _entry(session_label="mine", _path="/overlay/2026-07-24.json")
        labels = {r.text for r in text_rows([base, mine])}
        assert labels == {"upstream", "mine"}


class TestRawScanResponses:
    def test_a_did_scan_responder_is_filed_under_its_pid(self):
        scan = {"responding": [{"did": "F188", "notes": "Raw: 62F188" + b"95400G".hex()}]}
        (row,) = payload_rows([_entry(ecu="BCM", pid="scan 22 F180-F18F", scan_results=scan)])
        assert (row.ecu, row.pid, row.field, row.lossy) == ("BCM", "22F188", "scan notes", False)
        assert row.data == bytes.fromhex("62F188") + b"95400G"

    def test_a_discover_responder_is_filed_under_its_address(self):
        scan = {"responding": [{"tx": "0x7E0", "rx": "0x7E8", "notes": "Raw: 7E00"}]}
        (row,) = payload_rows([_entry(ecu="broadcast", pid="discover 700-7FF", scan_results=scan)])
        assert (row.ecu, row.pid, row.data, row.lossy) == (
            "0x7E8",
            "discover 700-7FF",
            b"\x7e\x00",
            False,
        )

    def test_a_truncated_note_is_lossy_and_drops_a_split_byte(self):
        scan = {"responding": [{"did": "01", "notes": "Raw: 6101AAB..."}]}
        (row,) = payload_rows([_entry(ecu="BMS", pid="scan 21 01-FF", scan_results=scan)])
        assert row.data == bytes.fromhex("6101AA") and row.lossy

    def test_a_vin_in_a_raw_note_is_found_by_vin(self, tmp_path, monkeypatch, capsys):
        vin = "SYNTHVXN012345678"  # synthetic
        note = "Raw: " + (bytes.fromhex("62F190") + vin.encode()).hex().upper()
        scan = {"responding": [{"did": "F190", "response": "20 bytes", "notes": note}]}
        _write(tmp_path, [{"rx": "0x7EA", "pid": "scan 22 F190-F190", "scan_results": scan}])
        assert _run(tmp_path, monkeypatch, ["--vin", "--json"]) == 0
        (finding,) = json.loads(capsys.readouterr().out)["findings"]
        assert (finding["field"], finding["text"]) == ("scan notes", vin)


_ECUS = {
    "BCM": {"identity": {"part_number": "95400G7470", "hw": 12, "flag": True, "list": [1]}},
    "VCU": {"identity": {"part_number": "36400-0E150", "empty": ""}},
    "AMP": {"identity": None},
    "SRS": "not a mapping",
}


class TestIdentityRows:
    def test_scalars_only_and_never_a_bool(self):
        rows = identity_rows(_ECUS)
        assert sorted(rows) == [
            ("BCM", "hw", "12"),
            ("BCM", "part_number", "95400G7470"),
            ("VCU", "part_number", "36400-0E150"),
        ]

    def test_only_filters_case_insensitively(self):
        assert {ecu for ecu, _, _ in identity_rows(_ECUS, only=["vcu"])} == {"VCU"}
        assert identity_rows(_ECUS, only=[]) == []


class TestCommandPaths:
    def test_bad_query_exits_2(self, tmp_path, monkeypatch, capsys):
        _write(tmp_path, [_cap("BCM", "22F188", _payload("F188", b"95400G7470"))])
        assert _run(tmp_path, monkeypatch, ["954", "--query", "BCM::X"]) == 2
        assert "bad --query" in capsys.readouterr().err

    def test_an_empty_selector_is_noted(self, tmp_path, monkeypatch, capsys):
        _write(tmp_path, [_cap("BCM", "22F188", _payload("F188", b"95400G7470"))])
        assert _run(tmp_path, monkeypatch, ["954", "--query", "BCM VCU"]) == 0
        assert "no captures matched selector 'VCU'" in capsys.readouterr().err

    def test_defs_survives_unloadable_definitions(self, tmp_path, monkeypatch, capsys):
        import yaml

        _write(tmp_path, [_cap("BCM", "22F188", _payload("F188", b"95400G7470"))])

        def broken(*a, **k):
            raise yaml.YAMLError("bad indentation")

        monkeypatch.setattr("canlib.pids.load_pids", broken)
        assert _run(tmp_path, monkeypatch, ["95400", "--defs", "--json"]) == 0
        captured = capsys.readouterr()
        assert "--defs skipped" in captured.err and "bad indentation" in captured.err
        assert {f["source"] for f in json.loads(captured.out)["findings"]} == {"payload"}


class TestLayeredProfile:
    def test_search_reads_both_layers(self, tmp_path, monkeypatch, capsys):
        from canlib import config, profile

        bundled, user = tmp_path / "profiles", tmp_path / "cfg" / "canair" / "profiles"
        monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "cfg"))
        monkeypatch.delenv("CANAIR_PROFILES_DIR", raising=False)
        monkeypatch.setattr("canlib.profile.BUNDLED_PROFILES_DIR", bundled)
        config.load_config.cache_clear()
        base = bundled / "ev6"
        (base / "ecus").mkdir(parents=True)
        (base / "profile.yaml").write_text('car_model: "ev6"\ninit: "ATSP6;"\n')
        (base / "ecus" / "bcm.yaml").write_text("BCM:\n  tx_id: 0x7A0\n")
        (user / "ev6" / "captures").mkdir(parents=True)
        (user / "ev6" / "profile.yaml").write_text("extends: ev6\n")
        (base / "captures").mkdir()
        # The same file name in both layers, each with its own session text.
        for root, label, part in ((base, "upstream", b"95400G7470"), (user / "ev6", "mine", b"")):
            caps = [{"rx": "0x7A8", "pid": "22F188", "payload": _payload("F188", part + b"\x00")}]
            doc = {"sessions": [{"date": "2026-07-24", "label": label, "captures": caps}]}
            (root / "captures" / "2026-07-24.json").write_text(json.dumps(doc))
        monkeypatch.setattr(profile, "_active", profile.resolve_profile("ev6"))
        try:
            parser = build_parser()
            for needle, field in (("upstream", "session label"), ("mine", "session label")):
                args = parser.parse_args(["search", "uds", needle, "--json"])
                assert args.func(args) == 0
                env = json.loads(capsys.readouterr().out)
                assert [f["field"] for f in env["findings"]] == [field]
            args = parser.parse_args(["search", "uds", "95400G", "--json", "--in", "payload"])
            assert args.func(args) == 0
            (finding,) = json.loads(capsys.readouterr().out)["findings"]
            assert finding["ecu"] == "BCM" and finding["text"] == "95400G7470"
        finally:
            profile._active = None
            config.load_config.cache_clear()


class TestKindNamedPattern:
    def test_double_dash_searches_for_the_text_uds(self):
        argv = _inject_default_subcommand(["search", "--", "uds"])
        args = build_parser().parse_args(argv)
        assert (args.search_kind, args.pattern) == ("uds", "uds")

    def test_a_bare_kind_word_is_the_kind(self):
        args = build_parser().parse_args(_inject_default_subcommand(["search", "uds"]))
        assert (args.search_kind, args.pattern) == ("uds", None)

    def test_the_help_says_how(self, capsys):
        parser = argparse.ArgumentParser()
        from canlib.commands import search

        group = search.add_parser(parser.add_subparsers())
        assert "canair search -- uds" in group.format_help()


@pytest.mark.parametrize(
    "argv",
    [
        ["abc", "--min-len", "4"],
        ["abc", "--all-runs"],
        ["--vin", "--all-runs"],
        ["--hex", "AA", "--min-len", "8"],
        ["--vin", "--case-sensitive"],
        ["--hex", "AA", "--defs"],
        ["--case-sensitive"],
        ["--hex", "0xA 0xB"],
    ],
)
def test_silently_ignored_combinations_are_usage_errors(tmp_path, monkeypatch, capsys, argv):
    _write(tmp_path, [_cap("BCM", "22F188", _payload("F188", b"95400G7470"))])
    assert _run(tmp_path, monkeypatch, argv) == 2
    assert "error:" in capsys.readouterr().err
