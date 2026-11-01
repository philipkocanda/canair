"""Tests for signal categories: the vocabulary, the unit table, validation, the AutoPID class
and the one-time migration off ``ha_class``."""

from __future__ import annotations

import argparse

import pytest
import yaml

from canlib.commands import pids as cli
from canlib.commands.validate import load_schema, validate_ecu_file
from canlib.pids_edit import migrate_categories, upsert_parameter
from canlib.pids_edit.categories import convert
from canlib.signal_category import (
    BINARY_CATEGORIES,
    CATEGORIES,
    UNIT_CATEGORIES,
    category_findings,
    category_of,
    unit_category,
)
from canlib.wican.autopid_class import autopid_class

DOOR = {"type": "enum", "values": {0: "closed", 1: "open"}}


class TestVocabulary:
    def test_every_unit_maps_into_the_vocabulary(self):
        assert set(UNIT_CATEGORIES.values()) <= set(CATEGORIES)

    def test_no_category_is_listed_twice(self):
        assert len(CATEGORIES) == len(set(CATEGORIES))

    @pytest.mark.parametrize(
        ("unit", "category"),
        [("V", "voltage"), ("°C", "temperature"), ("C", "temperature"), (" kW ", "power")],
    )
    def test_a_unit_implies_its_quantity(self, unit, category):
        assert unit_category(unit) == category

    @pytest.mark.parametrize("unit", ["%", "", "rpm", "Nm", None, 5])
    def test_an_ambiguous_or_unknown_unit_implies_nothing(self, unit):
        assert unit_category(unit) is None


class TestCategoryOf:
    def test_a_stated_category_wins(self):
        assert category_of({"unit": "%", "category": "state_of_charge"}) == "state_of_charge"

    def test_otherwise_the_unit_decides(self):
        assert category_of({"unit": "km/h"}) == "speed"

    def test_neither_is_none(self):
        assert category_of({"unit": "%"}) is None


class TestFindings:
    def test_nothing_stated_is_fine(self):
        assert category_findings({"unit": "V"}, "X") == ([], [])

    def test_an_unknown_value_is_an_error(self):
        errors, _ = category_findings({"category": "battery"}, "X")
        assert errors and "invalid category 'battery'" in errors[0]

    def test_a_category_the_unit_implies_is_an_error(self):
        errors, _ = category_findings({"unit": "V", "category": "voltage"}, "X")
        assert errors and "already implies category 'voltage'" in errors[0]

    def test_a_category_against_the_unit_is_an_error_too(self):
        errors, _ = category_findings({"unit": "V", "category": "door", **DOOR}, "X")
        assert errors

    def test_a_quantity_without_a_unit_may_be_stated(self):
        assert category_findings({"category": "power"}, "X") == ([], [])

    @pytest.mark.parametrize("category", BINARY_CATEGORIES)
    def test_a_binary_category_wants_a_two_state_enum(self, category):
        _, warnings = category_findings({"category": category}, "X")
        assert warnings and "two-state enum" in warnings[0]
        assert category_findings({"category": category, **DOOR}, "X") == ([], [])

    def test_a_three_state_enum_does_not_say_which_state_is_which(self):
        param = {"category": "lock", "type": "enum", "values": {0: "a", 1: "b", 2: "c"}}
        assert category_findings(param, "X")[1]


class TestAutopidClass:
    @pytest.mark.parametrize(
        ("param", "token"),
        [
            ({"unit": "V"}, "voltage"),
            ({"unit": "%", "category": "state_of_charge"}, "battery"),
            ({"category": "charging"}, "battery_charging"),
            ({"category": "door"}, "door"),
            ({"category": "lamp"}, ""),
            ({"unit": "%", "category": "state_of_health"}, ""),
            ({"unit": "%"}, ""),
        ],
    )
    def test_maps_onto_the_home_assistant_tokens(self, param, token):
        assert autopid_class(param) == token


class TestConvert:
    @pytest.mark.parametrize(
        ("ha_class", "unit", "category"),
        [
            ("voltage", "V", None),
            ("battery", "%", "state_of_charge"),
            ("light", "", "lamp"),
            ("battery_charging", None, "charging"),
            ("door", "", "door"),
            ("power", "", "power"),
            ("", "%", None),
            ("none", "", None),
            ("illuminance", "", None),
        ],
    )
    def test_each_old_value(self, ha_class, unit, category):
        assert convert(ha_class, unit)[0] == category


YAML = """\
TEST:
  tx_id: 0x700
  pids:
    2101:
      status: active
      parameters:
        SOC:
          expression: B10/2
          unit: "%"
          ha_class: battery
          notes: charge
        PACK_V:
          expression: "[B11:B12]/10"
          unit: V
          ha_class: voltage
        DOOR:
          expression: "B13:0"
          ha_class: door
          type: enum
          values:
            0: closed
            1: open
        OTHER:
          expression: B14
          ha_class: ""
        DONE:
          expression: B15
          unit: "%"
          ha_class: battery
          category: humidity
    2102:
      status: active
      unit: V
      ha_class: voltage
      parameters:
        CELL_1:
          expression: B10*0.02
        CELL_2:
          expression: B11*0.02
          unit: ""
"""


@pytest.fixture
def pids_dir(tmp_path):
    (tmp_path / "test.yaml").write_text(YAML)
    return tmp_path


def _pids(pids_dir):
    data = yaml.safe_load((pids_dir / "test.yaml").read_text())["TEST"]["pids"]
    return {str(k): v for k, v in data.items()}


class TestMigration:
    def test_dry_run_reports_and_writes_nothing(self, pids_dir):
        found = migrate_categories("TEST", dry_run=True, pids_dir=pids_dir)
        assert (pids_dir / "test.yaml").read_text() == YAML
        assert {(m.pid, m.param): m.category for m in found} == {
            ("2101", "SOC"): "state_of_charge",
            ("2101", "PACK_V"): None,
            ("2101", "DOOR"): "door",
            ("2101", "OTHER"): None,
            ("2101", "DONE"): "humidity",
            ("2102", None): None,
            ("2102", "CELL_2"): "voltage",
        }

    def test_rewrites_in_place(self, pids_dir):
        migrate_categories("TEST", pids_dir=pids_dir)
        text = (pids_dir / "test.yaml").read_text()
        assert "ha_class" not in text
        assert '          unit: "%"\n          category: state_of_charge\n          notes:' in text
        pids = _pids(pids_dir)
        params = pids["2101"]["parameters"]
        assert "category" not in params["PACK_V"] and "category" not in params["OTHER"]
        assert params["DOOR"]["category"] == "door"
        assert params["DONE"]["category"] == "humidity"
        # The PID default resolves into the one signal whose unit no longer implies it.
        assert "category" not in pids["2102"]["parameters"]["CELL_1"]
        assert pids["2102"]["parameters"]["CELL_2"]["category"] == "voltage"
        errors, warnings, _ = validate_ecu_file(pids_dir / "test.yaml", load_schema())
        assert not errors
        assert not [w for w in warnings if "category" in w or "ha_class" in w]

    def test_a_second_run_finds_nothing(self, pids_dir):
        migrate_categories("TEST", pids_dir=pids_dir)
        assert migrate_categories("TEST", pids_dir=pids_dir) == []

    def test_cli_over_every_ecu(self, pids_dir, capsys):
        args = argparse.Namespace(ecu=None, dir=pids_dir, dry_run=False, no_validate=False)
        assert cli.cmd_migrate_categories(args) == 0
        assert "Migrated 7 ha_class field(s): 4 to category, 3 removed." in capsys.readouterr().out


def test_validate_points_an_unmigrated_profile_at_the_migration(pids_dir):
    _, warnings, _ = validate_ecu_file(pids_dir / "test.yaml", load_schema())
    assert any("ha_class is retired" in w and "migrate-categories" in w for w in warnings)


def test_upsert_writes_and_an_empty_value_removes(pids_dir):
    migrate_categories("TEST", pids_dir=pids_dir)
    upsert_parameter("TEST", "2101", "SOC", "B10/2", category="", pids_dir=pids_dir)
    assert "category" not in _pids(pids_dir)["2101"]["parameters"]["SOC"]
    upsert_parameter("TEST", "2101", "SOC", "B10/2", category="state_of_charge", pids_dir=pids_dir)
    assert _pids(pids_dir)["2101"]["parameters"]["SOC"]["category"] == "state_of_charge"
