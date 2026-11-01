"""Tests for PID-level defaults: inheritance, the editor and hoisting."""

from __future__ import annotations

import argparse

import pytest
import yaml

from canlib.commands import pids as cli
from canlib.commands.validate import load_schema, validate_ecu_file, validate_pids_file
from canlib.pids import effective_param, load_pids
from canlib.pids_edit import PidsEditError, hoist_defaults, set_pid_defaults
from canlib.pids_edit.defaults import plan_hoist

LINK = "https://example.org/sheet"

YAML = f"""\
TEST:
  tx_id: 0x700
  pids:
    2101:
      status: active
      notes: page note
      parameters:
        A:
          expression: B10
          source: shared source
          source_links:
            - {LINK}
          verified: true
          notes: only A
        B:
          expression: B11
          source: shared source
          source_links:
            - {LINK}
          verified: false
        B2:
          expression: B13
          source: shared source
          source_links:
            - {LINK}
          verified: false
        C:
          expression: B12
          source: other source
          source_links:
            - {LINK}
    2102:
      status: active
      parameters:
        D:
          expression: B10
          source: shared source
        E:
          expression: B11
    2103:
      status: active
      parameters:
        F:
          expression: B10
          source: one
        G:
          expression: B11
          source: two
    2104:
      status: active
      parameters:
        CELL_1:
          expression: B10*0.02
          unit: V
          min: "0"
          max: "5"
          verified: true
        CELL_2:
          expression: B11*0.02
          unit: V
          min: "0"
          max: "5"
          verified: true
        CELL_4:
          expression: B13*0.02
          unit: V
          min: "0"
          max: "5"
          verified: true
        CELL_3:
          expression: B12*0.02
          unit: V
          min: "0"
          max: "5"
          verified: false
          notes: suspect
"""


@pytest.fixture
def pids_dir(tmp_path):
    (tmp_path / "test.yaml").write_text(YAML)
    return tmp_path


def _load(pids_dir):
    return yaml.safe_load((pids_dir / "test.yaml").read_text())["TEST"]["pids"]


def _load_text():
    return {str(k): v for k, v in yaml.safe_load(YAML)["TEST"]["pids"].items()}


class TestEffectiveParam:
    def test_inherits_what_the_parameter_omits(self):
        pid = {"source": "S", "source_links": ["u"], "unit": "V", "min": "0", "verified": True}
        got = effective_param(pid, {"expression": "B1"})
        assert got == {"expression": "B1", **pid}

    def test_own_value_wins(self):
        assert effective_param({"source": "S"}, {"source": "mine"})["source"] == "mine"

    def test_explicit_empty_and_false_are_overrides_not_gaps(self):
        pid = {"source": "S", "source_links": ["u"], "unit": "V", "verified": True}
        own = {"source": "", "source_links": [], "unit": "", "verified": False}
        assert effective_param(pid, own) == own

    def test_notes_are_not_inherited(self):
        assert "notes" not in effective_param({"notes": "page"}, {"expression": "B1"})

    def test_nothing_to_inherit_returns_the_definition_itself(self):
        param = {"expression": "B1"}
        assert effective_param({}, param) is param


class TestLoader:
    def test_loaded_parameters_carry_the_inherited_fields(self, pids_dir):
        (pids_dir / "test.yaml").write_text(
            "TEST:\n  tx_id: 0x700\n  pids:\n    2101:\n      status: active\n      unit: V\n"
            "      min: '0'\n      verified: true\n      parameters:\n        A:\n"
            "          expression: B10\n        B:\n          expression: B11\n"
            "          unit: A\n          verified: false\n"
        )
        params = load_pids(pids_dir)["ecus"]["TEST"]["pids"][2101]["parameters"]
        a, b = params["A"], params["B"]
        assert (a["unit"], a["min"], a["verified"]) == ("V", "0", True)
        assert (b["unit"], b["min"], b["verified"]) == ("A", "0", False)


class TestPlanHoist:
    def test_hoists_a_value_held_by_most_parameters_and_keeps_the_odd_one(self):
        plan = plan_hoist(_load_text()["2101"])
        assert plan["source"] == ("shared source", ("A", "B", "B2"))
        assert plan["source_links"][1] == ("A", "B", "B2", "C")

    def test_a_cell_voltage_group_shares_its_unit_range_and_verified(self):
        plan = plan_hoist(_load_text()["2104"])
        assert set(plan) == {"unit", "min", "max", "verified"}
        assert plan["unit"] == ("V", ("CELL_1", "CELL_2", "CELL_4", "CELL_3"))
        assert plan["verified"] == (True, ("CELL_1", "CELL_2", "CELL_4"))

    def test_a_value_held_by_half_or_less_is_not_a_default(self):
        pdef = {
            "parameters": {n: {"min": v} for n, v in zip("ABCD", ("0", "0", "1", "2"), strict=True)}
        }
        assert plan_hoist(pdef) == {}

    def test_a_pair_is_not_worth_a_default(self):
        pdef = {"parameters": {"A": {"unit": "V"}, "B": {"unit": "V"}, "C": {"unit": "V"}}}
        assert "unit" in plan_hoist(pdef)
        del pdef["parameters"]["C"]
        assert plan_hoist(pdef) == {}

    def test_a_parameter_without_the_field_blocks_it(self):
        assert plan_hoist(_load_text()["2102"]) == {}

    def test_all_distinct_values_leave_nothing_to_share(self):
        assert plan_hoist(_load_text()["2103"]) == {}

    def test_single_parameter_pid_is_left_alone(self):
        assert plan_hoist({"parameters": {"A": {"source": "s"}}}) == {}

    def test_a_pid_that_already_states_it_is_left_alone(self):
        pdef = _load_text()["2101"]
        assert "source" not in plan_hoist({**pdef, "source": "already"})

    def test_a_numeric_value_is_not_confused_with_its_string(self):
        params = {"A": {"min": 0}, "B": {"min": "0"}, "C": {"min": 0}, "D": {"min": 0}}
        assert plan_hoist({"parameters": params})["min"] == (0, ("A", "C", "D"))


class TestHoist:
    def test_moves_shared_values_up_and_leaves_the_override(self, pids_dir):
        moved = hoist_defaults("TEST", "2101", pids_dir=pids_dir)
        assert {(h.pid, h.field) for h in moved} == {("2101", "source"), ("2101", "source_links")}
        pid = _load(pids_dir)[2101]
        assert pid["source"] == "shared source"
        assert pid["source_links"] == [LINK]
        params = pid["parameters"]
        assert all("source" not in params[n] for n in ("A", "B", "B2"))
        assert params["C"]["source"] == "other source"
        assert "source_links" not in params["C"]

    def test_a_cell_group_collapses_to_its_pid(self, pids_dir):
        hoist_defaults("TEST", "2104", pids_dir=pids_dir)
        text = (pids_dir / "test.yaml").read_text()
        assert (
            "    2104:\n      status: active\n      unit: V\n"
            '      min: "0"\n      max: "5"\n      verified: true\n      parameters:\n'
        ) in text
        cell3 = _load(pids_dir)[2104]["parameters"]["CELL_3"]
        assert cell3 == {"expression": "B12*0.02", "verified": False, "notes": "suspect"}

    def test_no_parameter_changes_what_it_resolves_to(self, pids_dir):
        before = _load_text()
        hoist_defaults("TEST", pids_dir=pids_dir)
        after = {str(k): v for k, v in _load(pids_dir).items()}
        for pid, pdef in before.items():
            for name, param in pdef["parameters"].items():
                assert effective_param(after[pid], after[pid]["parameters"][name]) == (
                    effective_param(pdef, param)
                )

    def test_everything_else_in_the_file_is_untouched(self, pids_dir):
        hoist_defaults("TEST", pids_dir=pids_dir)
        text = (pids_dir / "test.yaml").read_text()
        assert "      notes: page note\n      source: shared source\n" in text
        assert "          notes: only A\n" in text
        assert "          notes: suspect\n" in text

    def test_dry_run_writes_nothing(self, pids_dir):
        before = (pids_dir / "test.yaml").read_text()
        assert hoist_defaults("TEST", dry_run=True, pids_dir=pids_dir)
        assert (pids_dir / "test.yaml").read_text() == before

    def test_can_be_narrowed_to_one_pid(self, pids_dir):
        assert hoist_defaults("TEST", "2102", pids_dir=pids_dir) == []
        assert "source" not in _load(pids_dir)[2101]

    def test_is_idempotent(self, pids_dir):
        hoist_defaults("TEST", pids_dir=pids_dir)
        text = (pids_dir / "test.yaml").read_text()
        assert hoist_defaults("TEST", pids_dir=pids_dir) == []
        assert (pids_dir / "test.yaml").read_text() == text

    def test_unknown_pid_is_an_error(self, pids_dir):
        with pytest.raises(PidsEditError):
            hoist_defaults("TEST", "9999", pids_dir=pids_dir)

    def test_result_validates(self, pids_dir):
        hoist_defaults("TEST", pids_dir=pids_dir)
        ok, out = validate_pids_file(pids_dir / "test.yaml")
        assert ok, out


class TestSetDefaults:
    def test_new_fields_go_above_parameters_in_canonical_order(self, pids_dir):
        set_pid_defaults(
            "TEST", "2102", {"max": "5", "unit": "V", "verified": True}, pids_dir=pids_dir
        )
        text = (pids_dir / "test.yaml").read_text()
        assert (
            '    2102:\n      status: active\n      unit: V\n      max: "5"\n'
            "      verified: true\n      parameters:"
        ) in text

    def test_replaces_in_place(self, pids_dir):
        set_pid_defaults("TEST", "2102", {"unit": "V"}, pids_dir=pids_dir)
        set_pid_defaults("TEST", "2102", {"unit": "A"}, pids_dir=pids_dir)
        assert _load(pids_dir)[2102]["unit"] == "A"
        assert (pids_dir / "test.yaml").read_text().count("\n      unit:") == 1

    def test_sets_links_and_clears_fields(self, pids_dir):
        links = ["https://a", "https://b"]
        set_pid_defaults("TEST", "2102", {"source_links": links, "unit": "V"}, pids_dir=pids_dir)
        assert _load(pids_dir)[2102]["source_links"] == links
        set_pid_defaults("TEST", "2102", {}, clear=["source_links", "unit"], pids_dir=pids_dir)
        assert {"source_links", "unit"}.isdisjoint(_load(pids_dir)[2102])

    @pytest.mark.parametrize(
        "fields, clear",
        [
            ({"expression": "B1"}, ()),
            ({"notes": "x"}, ()),
            ({"unit": ""}, ()),
            ({"unit": "V"}, ("unit",)),
            ({}, ()),
        ],
    )
    def test_refuses_what_cannot_be_shared(self, pids_dir, fields, clear):
        before = (pids_dir / "test.yaml").read_text()
        with pytest.raises(PidsEditError):
            set_pid_defaults("TEST", "2102", fields, clear=clear, pids_dir=pids_dir)
        assert (pids_dir / "test.yaml").read_text() == before

    def test_unknown_pid_is_an_error_and_leaves_the_file_alone(self, pids_dir):
        before = (pids_dir / "test.yaml").read_text()
        with pytest.raises(PidsEditError):
            set_pid_defaults("TEST", "9999", {"unit": "V"}, pids_dir=pids_dir)
        assert (pids_dir / "test.yaml").read_text() == before


class TestValidation:
    def _validate(self, pids_dir, extra):
        text = YAML.replace("      notes: page note\n", f"      notes: page note\n{extra}")
        (pids_dir / "test.yaml").write_text(text)
        return validate_pids_file(pids_dir / "test.yaml")

    def test_pid_level_fields_are_known(self, pids_dir):
        extra = (
            f"      unit: V\n      min: '0'\n      verified: true\n      source_links: [{LINK}]\n"
        )
        ok, out = self._validate(pids_dir, extra)
        assert ok and "unknown PID field" not in out

    def test_source_must_be_a_string(self, pids_dir):
        ok, out = self._validate(pids_dir, "      source: [a, b]\n")
        assert not ok and "source must be a string" in out

    def test_verified_must_be_a_boolean(self, pids_dir):
        ok, out = self._validate(pids_dir, "      verified: maybe\n")
        assert not ok and "verified must be a boolean" in out

    def test_links_must_be_a_list_of_strings(self, pids_dir):
        ok, out = self._validate(pids_dir, "      source_links: just-a-string\n")
        assert not ok and "source_links must be a list of strings" in out

    def test_an_inherited_verified_counts_as_verified(self, pids_dir):
        (pids_dir / "test.yaml").write_text(
            "TEST:\n  tx_id: 0x700\n  pids:\n    2101:\n      status: active\n"
            "      verified: true\n      parameters:\n        A:\n          expression: B10\n"
        )
        errors, _, stats = validate_ecu_file(pids_dir / "test.yaml", load_schema())
        assert not errors and stats["verified"] == 1 and stats["unverified"] == 0


def test_cli_hoist_and_dry_run(pids_dir, capsys):
    args = argparse.Namespace(ecu="TEST", pid=None, dir=pids_dir, dry_run=True, no_validate=False)
    assert cli.cmd_hoist_defaults(args) == 0
    assert "Would hoist 6 field(s) across 2 PID(s)" in capsys.readouterr().out
    args.dry_run = False
    assert cli.cmd_hoist_defaults(args) == 0
    assert "Hoisted 6 field(s)" in capsys.readouterr().out
    assert _load(pids_dir)[2101]["source"] == "shared source"


def test_cli_hoist_without_ecu_sweeps_every_file(pids_dir):
    args = argparse.Namespace(ecu=None, pid=None, dir=pids_dir, dry_run=False, no_validate=False)
    assert cli.cmd_hoist_defaults(args) == 0
    assert _load(pids_dir)[2104]["unit"] == "V"


def test_cli_set_pid_defaults(pids_dir):
    args = argparse.Namespace(
        ecu="TEST",
        pid="2102",
        dir=pids_dir,
        no_validate=False,
        unit="V",
        min="0",
        max="5",
        verified=True,
        source=None,
        source_link=None,
        clear=None,
    )
    assert cli.cmd_set_pid_defaults(args) == 0
    pid = _load(pids_dir)[2102]
    assert (pid["unit"], pid["min"], pid["max"], pid["verified"]) == (
        "V",
        "0",
        "5",
        True,
    )
