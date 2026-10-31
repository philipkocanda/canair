"""Dialect repairs that let ``import dbc`` read real-world DBCs cantools rejects.

Each case is a minimal reproduction of a construct found in commaai/opendbc (or
another public DBC) that makes cantools refuse the whole file.
"""

from __future__ import annotations

import argparse
import json

import pytest
import yaml

import canlib.profile as profile
from canlib.dbc_repair import DbcLoadError, load_dbc, repair_dbc_text, summarize
from canlib.profile import Profile

HEADER = 'VERSION ""\n\nNS_ :\n\nBS_:\n\nBU_: XXX\n\n'
MESSAGE = 'BO_ 100 A: 8 XXX\n SG_ S : 0|8@1+ (1,0) [0|255] "" XXX\n\n'


def _dbc(tmp_path, body: str, header: str = HEADER, name: str = "car.dbc"):
    path = tmp_path / name
    path.write_text(header + body, encoding="cp1252")
    return path


def _kinds(repairs) -> list[str]:
    return [r.kind for r in repairs]


@pytest.fixture
def temp_profile(tmp_path):
    profile._active = Profile(name="test", root=tmp_path)
    return Profile(name="test", root=tmp_path)


def _import(argv):
    from canlib.commands import import_ as imp

    p = imp.add_parser(argparse.ArgumentParser().add_subparsers())
    return imp.run(p.parse_args(argv))


class TestLoad:
    def test_clean_file_is_not_repaired(self, tmp_path):
        db, repairs = load_dbc(_dbc(tmp_path, MESSAGE + 'CM_ BO_ 100 "fine";\n'))
        assert repairs == []
        assert [m.name for m in db.messages] == ["A"]

    def test_comment_without_object_keyword_is_dropped(self, tmp_path):
        # opendbc hyundai_can.dbc: `CM_ 145 "…";` (no BO_)
        db, repairs = load_dbc(_dbc(tmp_path, MESSAGE + 'CM_ 100 "no keyword";\n'))
        assert _kinds(repairs) == ["comment"]
        assert repairs[0].line == HEADER.count("\n") + 4
        assert db.get_message_by_frame_id(100).signals[0].name == "S"

    def test_comment_without_signal_name_is_dropped(self, tmp_path):
        # opendbc _honda_common.dbc: `CM_ SG_ 304 "…";`
        _, repairs = load_dbc(_dbc(tmp_path, MESSAGE + 'CM_ SG_ 100 "no signal";\n'))
        assert _kinds(repairs) == ["comment"]

    def test_comment_without_semicolon_is_dropped(self, tmp_path):
        # opendbc toyota_radar_dsu_tssp.dbc: a network comment with no closing `;`
        _, repairs = load_dbc(_dbc(tmp_path, 'CM_ "Front target"\n' + MESSAGE))
        assert _kinds(repairs) == ["comment"]

    def test_multiline_comment_keeps_later_line_numbers(self, tmp_path):
        body = MESSAGE + 'CM_ 100 "two\nlines";\nCM_ SG_ 100 "x";\n'
        _, repairs = load_dbc(_dbc(tmp_path, body))
        first = HEADER.count("\n") + 4
        assert [r.line for r in repairs] == [first, first + 2]

    def test_unflagged_29bit_id_becomes_extended(self, tmp_path):
        # opendbc vw_meb.dbc: `BO_ 316495015 VZE_04` (0x12DD54A7) without bit 31
        body = (
            'BO_ 316495015 E: 8 XXX\n SG_ S : 0|8@1+ (1,0) [0|255] "" XXX\n\n'
            'CM_ SG_ 316495015 S "kept";\nVAL_ 316495015 S 1 "on" 0 "off" ;\n'
        )
        db, repairs = load_dbc(_dbc(tmp_path, body))
        assert _kinds(repairs) == ["extended_id"]
        msg = db.messages[0]
        assert (msg.frame_id, msg.is_extended_frame) == (0x12DD54A7, True)
        assert dict(msg.signals[0].choices) == {1: "on", 0: "off"}

    def test_flagged_and_standard_ids_are_untouched(self, tmp_path):
        text = HEADER + MESSAGE + 'BO_ 2147484648 F: 8 XXX\n SG_ T : 0|8@1+ (1,0) [0|1] "" XXX\n'
        assert repair_dbc_text(text) == (text, [])

    def test_bare_decimal_gets_leading_zero(self, tmp_path):
        # opendbc _stellantis_common.dbc: `(.25,-500)`
        body = 'BO_ 100 A: 8 XXX\n SG_ S : 0|13@1+ (.25,-500) [-.5|1547.5] "Nm" XXX\n\n'
        db, repairs = load_dbc(_dbc(tmp_path, body))
        assert _kinds(repairs) == ["leading_zero"]
        sig = db.messages[0].signals[0]
        assert (sig.scale, sig.minimum) == (0.25, -0.5)

    def test_digit_first_names_are_prefixed_everywhere(self, tmp_path):
        # opendbc psa_aee2010_r3.dbc `0_COUNTER`, mazda_2017.dbc message `2017_5`
        body = (
            'BO_ 100 2017_5: 8 XXX\n SG_ 0_COUNTER : 0|4@1+ (1,0) [0|15] "" XXX\n\n'
            'CM_ SG_ 100 0_COUNTER "0_COUNTER wraps";\nVAL_ 100 0_COUNTER 0 "zero" ;\n'
        )
        db, repairs = load_dbc(_dbc(tmp_path, body))
        assert sorted(r.detail for r in repairs) == ["0_COUNTER -> _0_COUNTER", "2017_5 -> _2017_5"]
        msg = db.messages[0]
        assert msg.name == "_2017_5"
        assert msg.signals[0].name == "_0_COUNTER"
        assert dict(msg.signals[0].choices) == {0: "zero"}
        assert msg.signals[0].comment == "0_COUNTER wraps"  # text inside strings untouched

    def test_value_table_without_semicolon_is_closed(self, tmp_path):
        # opendbc mazda_2017.dbc / _stellantis_common.dbc
        body = MESSAGE + 'VAL_ 100 S 1 "on" 0 "off"\nVAL_ 100 S 2 "x" ;\n'
        _, repairs = load_dbc(_dbc(tmp_path, body))
        assert _kinds(repairs) == ["terminator"]

    def test_missing_bs_and_bu_sections_are_added(self, tmp_path):
        # projectgus kona-ev-dbc pcan.dbc: NS_ followed directly by BO_
        db, repairs = load_dbc(_dbc(tmp_path, MESSAGE, header='VERSION ""\n\nNS_ :\n\n'))
        assert _kinds(repairs) == ["header"]
        assert db.messages[0].name == "A"

    def test_header_not_added_without_ns_section(self):
        assert repair_dbc_text(MESSAGE) == (MESSAGE, [])

    def test_unrepairable_file_reports_the_original_error(self, tmp_path):
        with pytest.raises(DbcLoadError, match="more than 11 bits"):
            # An unflagged ID above 0x1FFFFFFF is no valid frame at all.
            load_dbc(_dbc(tmp_path, 'BO_ 1075053113 X: 8 XXX\n SG_ S : 0|8@1+ (1,0) [0|1] "" X\n'))

    def test_summary_groups_by_kind(self, tmp_path):
        body = MESSAGE + "".join(f'CM_ 100 "c{i}";\n' for i in range(7))
        _, repairs = load_dbc(_dbc(tmp_path, body))
        (line,) = summarize(repairs, limit=2)
        assert line.startswith("comment (7): line ") and line.endswith("+5 more")


class TestImport:
    BODY = (
        'BO_ 316495015 E: 8 XXX\n SG_ S : 0|8@1+ (1,0) [0|255] "" XXX\n\n'
        'CM_ 316495015 "no keyword";\n'
    )

    def test_import_reports_repairs_and_stores_extended_frame(self, temp_profile, tmp_path, capsys):
        path = _dbc(tmp_path, self.BODY)
        assert _import(["dbc", str(path), "--bus", "pt"]) == 0
        err = capsys.readouterr().err
        assert "does not parse as written" in err
        assert "extended_id (1)" in err and "comment (1)" in err
        stored = yaml.safe_load((temp_profile.signals_dir / "pt.yaml").read_text())["messages"]
        assert list(stored) == ["0x12DD54A7"]
        assert path.read_text(encoding="cp1252").endswith('"no keyword";\n')  # file untouched

    def test_json_lists_repairs(self, temp_profile, tmp_path, capsys):
        path = _dbc(tmp_path, self.BODY)
        assert _import(["dbc", str(path), "--bus", "pt", "--dry-run", "--json"]) == 0
        out = json.loads(capsys.readouterr().out)
        assert {r["kind"] for r in out["repairs"]} == {"extended_id", "comment"}

    def test_clean_import_is_silent_about_repairs(self, temp_profile, tmp_path, capsys):
        assert _import(["dbc", str(_dbc(tmp_path, MESSAGE)), "--bus", "pt", "--dry-run"]) == 0
        assert "repaired" not in capsys.readouterr().err
