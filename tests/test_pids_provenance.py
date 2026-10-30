"""Tests for PID-level ``source``/``source_links``: inheritance, editors and hoisting."""

from __future__ import annotations

import argparse

import pytest
import yaml

from canlib.commands import pids as cli
from canlib.commands.validate import validate_pids_file
from canlib.pids import effective_param
from canlib.pids_edit import (
    PidsEditError,
    hoist_provenance,
    set_pid_source,
    set_pid_source_links,
)
from canlib.pids_edit.provenance import plan_hoist

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
"""


@pytest.fixture
def pids_dir(tmp_path):
    (tmp_path / "test.yaml").write_text(YAML)
    return tmp_path


def _load(pids_dir):
    return yaml.safe_load((pids_dir / "test.yaml").read_text())["TEST"]["pids"]


class TestEffectiveParam:
    def test_inherits_what_the_parameter_omits(self):
        got = effective_param({"source": "S", "source_links": ["u"]}, {"expression": "B1"})
        assert got == {"expression": "B1", "source": "S", "source_links": ["u"]}

    def test_own_value_wins(self):
        assert effective_param({"source": "S"}, {"source": "mine"})["source"] == "mine"

    def test_explicit_empty_is_an_override_not_a_gap(self):
        got = effective_param(
            {"source": "S", "source_links": ["u"]}, {"source": "", "source_links": []}
        )
        assert got["source"] == "" and got["source_links"] == []

    def test_notes_are_not_inherited(self):
        assert "notes" not in effective_param({"notes": "page"}, {"expression": "B1"})

    def test_nothing_to_inherit_returns_the_definition_itself(self):
        param = {"expression": "B1"}
        assert effective_param({}, param) is param


class TestPlanHoist:
    def test_hoists_a_value_shared_by_two_parameters_and_keeps_the_odd_one(self):
        pdef = _load_text()["2101"]
        plan = plan_hoist(pdef)
        assert plan["source"] == ("shared source", ("A", "B"))
        assert plan["source_links"][1] == ("A", "B", "C")

    def test_a_parameter_without_the_field_blocks_it(self):
        assert plan_hoist(_load_text()["2102"]) == {}

    def test_all_distinct_values_leave_nothing_to_share(self):
        assert plan_hoist(_load_text()["2103"]) == {}

    def test_single_parameter_pid_is_left_alone(self):
        assert plan_hoist({"parameters": {"A": {"source": "s"}}}) == {}

    def test_a_pid_that_already_states_it_is_left_alone(self):
        pdef = _load_text()["2101"]
        assert "source" not in plan_hoist({**pdef, "source": "already"})


def _load_text():
    return {str(k): v for k, v in yaml.safe_load(YAML)["TEST"]["pids"].items()}


class TestHoist:
    def test_moves_shared_values_up_and_leaves_the_override(self, pids_dir):
        moved = hoist_provenance("TEST", pids_dir=pids_dir)
        assert {(h.pid, h.field) for h in moved} == {("2101", "source"), ("2101", "source_links")}
        pid = _load(pids_dir)[2101]
        assert pid["source"] == "shared source"
        assert pid["source_links"] == [LINK]
        params = pid["parameters"]
        assert "source" not in params["A"] and "source" not in params["B"]
        assert params["C"]["source"] == "other source"
        assert "source_links" not in params["C"]

    def test_no_parameter_changes_what_it_resolves_to(self, pids_dir):
        before = _load_text()
        hoist_provenance("TEST", pids_dir=pids_dir)
        after = _load(pids_dir)
        for pid, pdef in before.items():
            for name, param in pdef["parameters"].items():
                old = effective_param(pdef, param)
                new = effective_param(after[int(pid)], after[int(pid)]["parameters"][name])
                assert (old.get("source"), old.get("source_links")) == (
                    new.get("source"),
                    new.get("source_links"),
                )

    def test_everything_else_in_the_file_is_untouched(self, pids_dir):
        hoist_provenance("TEST", pids_dir=pids_dir)
        text = (pids_dir / "test.yaml").read_text()
        assert "      notes: page note\n      source: shared source\n" in text
        assert "          notes: only A\n" in text
        assert text.count("source: shared source") == 2  # PID 2101 and the untouched 2102

    def test_dry_run_writes_nothing(self, pids_dir):
        before = (pids_dir / "test.yaml").read_text()
        assert hoist_provenance("TEST", dry_run=True, pids_dir=pids_dir)
        assert (pids_dir / "test.yaml").read_text() == before

    def test_can_be_narrowed_to_one_pid(self, pids_dir):
        assert hoist_provenance("TEST", "2102", pids_dir=pids_dir) == []
        assert "source" not in _load(pids_dir)[2101]

    def test_is_idempotent(self, pids_dir):
        hoist_provenance("TEST", pids_dir=pids_dir)
        text = (pids_dir / "test.yaml").read_text()
        assert hoist_provenance("TEST", pids_dir=pids_dir) == []
        assert (pids_dir / "test.yaml").read_text() == text

    def test_unknown_pid_is_an_error(self, pids_dir):
        with pytest.raises(PidsEditError):
            hoist_provenance("TEST", "9999", pids_dir=pids_dir)

    def test_result_validates(self, pids_dir):
        hoist_provenance("TEST", pids_dir=pids_dir)
        ok, out = validate_pids_file(pids_dir / "test.yaml")
        assert ok, out


class TestSetters:
    def test_set_source_goes_above_parameters(self, pids_dir):
        set_pid_source("TEST", "2102", "a sheet", pids_dir=pids_dir)
        text = (pids_dir / "test.yaml").read_text()
        assert "    2102:\n      status: active\n      source: a sheet\n      parameters:" in text

    def test_set_source_replaces_in_place(self, pids_dir):
        set_pid_source("TEST", "2102", "first", pids_dir=pids_dir)
        set_pid_source("TEST", "2102", "second", pids_dir=pids_dir)
        assert _load(pids_dir)[2102]["source"] == "second"
        assert (pids_dir / "test.yaml").read_text().count("source: second") == 1

    def test_blank_source_clears(self, pids_dir):
        set_pid_source("TEST", "2102", "first", pids_dir=pids_dir)
        set_pid_source("TEST", "2102", None, pids_dir=pids_dir)
        assert "source" not in _load(pids_dir)[2102]

    def test_set_and_clear_links(self, pids_dir):
        set_pid_source_links("TEST", "2102", ["https://a", "https://b"], pids_dir=pids_dir)
        assert _load(pids_dir)[2102]["source_links"] == ["https://a", "https://b"]
        set_pid_source_links("TEST", "2102", [], pids_dir=pids_dir)
        assert "source_links" not in _load(pids_dir)[2102]

    def test_unknown_pid_is_an_error_and_leaves_the_file_alone(self, pids_dir):
        before = (pids_dir / "test.yaml").read_text()
        with pytest.raises(PidsEditError):
            set_pid_source("TEST", "9999", "x", pids_dir=pids_dir)
        assert (pids_dir / "test.yaml").read_text() == before


class TestValidation:
    def _validate(self, pids_dir, extra):
        text = YAML.replace("      notes: page note\n", f"      notes: page note\n{extra}")
        (pids_dir / "test.yaml").write_text(text)
        return validate_pids_file(pids_dir / "test.yaml")

    def test_pid_level_fields_are_known(self, pids_dir):
        ok, out = self._validate(pids_dir, f"      source: s\n      source_links: [{LINK}]\n")
        assert ok and "unknown PID field" not in out

    def test_source_must_be_a_string(self, pids_dir):
        ok, out = self._validate(pids_dir, "      source: [a, b]\n")
        assert not ok and "source must be a string" in out

    def test_links_must_be_a_list_of_strings(self, pids_dir):
        ok, out = self._validate(pids_dir, "      source_links: just-a-string\n")
        assert not ok and "source_links must be a list of strings" in out


def test_cli_hoist_and_dry_run(pids_dir, capsys):
    args = argparse.Namespace(ecu="TEST", pid=None, dir=pids_dir, dry_run=True, no_validate=False)
    assert cli.cmd_hoist_provenance(args) == 0
    assert "Would hoist 2 field(s)" in capsys.readouterr().out
    args.dry_run = False
    assert cli.cmd_hoist_provenance(args) == 0
    assert "Hoisted 2 field(s)" in capsys.readouterr().out
    assert _load(pids_dir)[2101]["source"] == "shared source"


def test_cli_hoist_without_ecu_sweeps_every_file(pids_dir, capsys):
    args = argparse.Namespace(ecu=None, pid=None, dir=pids_dir, dry_run=False, no_validate=False)
    assert cli.cmd_hoist_provenance(args) == 0
    assert _load(pids_dir)[2101]["source"] == "shared source"
