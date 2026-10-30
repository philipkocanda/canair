"""Tests for ``variants.yaml``: its checker, loader, editor and commands.

Plan: ``plans/2026-10-30-profile-variants-2-variant-registry.md``.
"""

from __future__ import annotations

import argparse
import json
import re
from pathlib import Path

import pytest
import yaml

from canlib import variants as v
from canlib.variants import edit
from canlib.variants.edit import VariantsEditError

SCHEMA = Path(v.__file__).resolve().parent.parent / "schema" / "variants_schema.yaml"


@pytest.fixture
def prof(tmp_path, monkeypatch):
    """A writable throwaway profile, active for the commands under test."""
    from canlib import profile as profile_mod
    from canlib.profile import Profile

    root = tmp_path / "kia-ev6-cv-ev"
    (root / "ecus").mkdir(parents=True)
    (root / "profile.yaml").write_text('car_model: "Kia EV6 (CV)"\ninit: "ATSP6;"\n')
    p = Profile(root.name, root)
    monkeypatch.setattr(profile_mod, "_active", p)
    return p


def _doc(**variants) -> dict:
    return {"variants": variants}


class TestCheck:
    def test_a_full_entry_is_valid(self):
        doc = _doc(
            **{
                "28kwh": {
                    "name": "28 kWh",
                    "model_code": "AEEV",
                    "production": ["2016", "2019-05"],
                    "model_years": [2017, 2019],
                    "markets": ["EU"],
                    "battery": {"usable_kwh": 28, "series_cells": 96, "cooling": "air"},
                    "status": "verified",
                    "sources": ["a source"],
                }
            }
        )
        assert v.check_variants_doc(doc) == []

    def test_no_file_content_is_valid(self):
        assert v.check_variants_doc(None) == []

    @pytest.mark.parametrize("vid", ["28", "28KWH-", "-x", "a--b", "has space", "a_b"])
    def test_bad_ids(self, vid):
        errors = v.check_variants_doc(_doc(**{vid: {"name": "x"}}))
        assert errors, vid

    def test_uppercase_ids_normalise(self):
        assert v.check_variants_doc(_doc(**{"28KWH": {"name": "x"}})) == []

    def test_name_is_required(self):
        assert any("missing required field 'name'" in e for e in v.check_variants_doc(_doc(a={})))

    @pytest.mark.parametrize("key", v.RESERVED_FIELDS)
    def test_reserved_fields_are_rejected(self, key):
        errors = v.check_variants_doc(_doc(a={"name": "x", key: {}}))
        assert any("not supported yet" in e for e in errors)

    def test_unknown_field(self):
        assert any(
            "unknown field 'colour'" in e
            for e in v.check_variants_doc(_doc(a={"name": "x", "colour": "red"}))
        )

    @pytest.mark.parametrize(
        ("key", "value"),
        [
            ("production", [2016, 2019]),  # unquoted years read back as numbers
            ("production", ["2019", "2016"]),
            ("production", ["2016-13", "2019"]),
            ("model_years", [2019, 2017]),
            ("status", "maybe"),
            ("markets", "EU"),
            ("battery", {"usable_kwh": -1}),
            ("battery", {"series_cells": 96.5}),
            ("battery", {"voltage": 360}),
        ],
    )
    def test_bad_values(self, key, value):
        assert v.check_variants_doc(_doc(a={"name": "x", key: value})), (key, value)

    def test_battery_id_must_agree_with_usable_capacity(self):
        errors = v.check_variants_doc(
            _doc(**{"38kwh": {"name": "x", "battery": {"usable_kwh": 28}}})
        )
        assert any("names 38 kWh" in e for e in errors)
        assert (
            v.check_variants_doc(_doc(**{"38kwh": {"name": "x", "battery": {"usable_kwh": 38.3}}}))
            == []
        )

    def test_battery_id_falls_back_to_gross_capacity(self):
        doc = _doc(**{"87kwh-lr": {"name": "x", "battery": {"gross_kwh": 66}}})
        assert any("battery.gross_kwh" in e for e in v.check_variants_doc(doc))


class TestLoad:
    def test_absent_file_is_empty(self, prof):
        assert v.load_variants(prof) == {}

    def test_loads_typed_variants(self, prof):
        prof.variants_file.write_text(
            'variants:\n  58KWH:\n    name: "58 kWh"\n    production: ["2021", "2024"]\n'
            "    battery: {gross_kwh: 58}\n    markets: [EU]\n"
        )
        (variant,) = v.load_variants(prof).values()
        assert variant.id == "58kwh"
        assert variant.production == ("2021", "2024")
        assert variant.battery.gross_kwh == 58
        assert variant.markets == ("EU",)
        assert v.describe(variant) == "58 kWh · built 2021 – 2024 · 58 kWh"

    def test_invalid_file_raises_with_every_problem(self, prof):
        prof.variants_file.write_text("variants:\n  '28':\n    colour: red\n")
        with pytest.raises(v.VariantsError) as e:
            v.load_variants(prof)
        assert "invalid variant ID" in str(e.value) and "unknown field" in str(e.value)


class TestEdit:
    def test_add_scaffolds_and_types_values(self, prof):
        edit.add_variant(
            "77kwh",
            "77.4 kWh",
            [
                ("production", "2022,2024-02"),
                ("model_years", "2022,2024"),
                ("markets", "EU, US"),
                ("battery.gross_kwh", "77.4"),
                ("battery.series_cells", "192"),
                ("sources", "a source, with a comma"),
                ("sources", "another source"),
                ("status", "seed"),
            ],
            profile=prof,
        )
        text = prof.variants_file.read_text()
        assert text.startswith("# The variants of this car")
        data = yaml.safe_load(text)["variants"]["77kwh"]
        assert data["production"] == ["2022", "2024-02"]
        assert data["model_years"] == [2022, 2024]
        assert data["markets"] == ["EU", "US"]
        assert data["battery"] == {"gross_kwh": 77.4, "series_cells": 192}
        assert data["sources"] == ["a source, with a comma", "another source"]
        # Canonical field order, regardless of the order given.
        assert list(data) == [
            "name",
            "production",
            "model_years",
            "markets",
            "battery",
            "status",
            "sources",
        ]

    def test_set_unset_rename_remove(self, prof):
        edit.add_variant("58kwh", "58 kWh", profile=prof)
        edit.set_variant_meta(
            "58kwh", [("generation", "NE"), ("battery.gross_kwh", "58")], profile=prof
        )
        edit.unset_variant_meta("58kwh", ["battery.gross_kwh"], profile=prof)
        edit.rename_variant("58kwh", "58kwh-eu", profile=prof)
        assert list(v.load_variants(prof)) == ["58kwh-eu"]
        assert v.load_variants(prof)["58kwh-eu"].generation == "NE"
        assert (
            "battery" not in yaml.safe_load(prof.variants_file.read_text())["variants"]["58kwh-eu"]
        )
        edit.remove_variant("58kwh-eu", profile=prof)
        assert v.load_variants(prof) == {}

    def test_comments_survive_an_edit(self, prof):
        prof.variants_file.write_text("# keep me\nvariants:\n  a1:\n    name: A  # and me\n")
        edit.set_variant_meta("a1", [("status", "seed")], profile=prof)
        text = prof.variants_file.read_text()
        assert "# keep me" in text and "# and me" in text

    def test_a_failing_edit_is_reverted(self, prof):
        edit.add_variant("38kwh", "38 kWh", [("battery.usable_kwh", "38.3")], profile=prof)
        before = prof.variants_file.read_text()
        with pytest.raises(VariantsEditError, match="reverted"):
            edit.set_variant_meta("38kwh", [("battery.usable_kwh", "28")], profile=prof)
        assert prof.variants_file.read_text() == before

    def test_a_failing_first_edit_leaves_no_file(self, prof):
        with pytest.raises(VariantsEditError):
            edit.add_variant("38kwh", "38 kWh", [("battery.usable_kwh", "28")], profile=prof)
        assert not prof.variants_file.exists()

    @pytest.mark.parametrize(
        ("pairs", "message"),
        [
            ([("colour", "red")], "unknown field"),
            ([("battery", "28")], "one at a time"),
            ([("battery.volts", "360")], "unknown battery field"),
            ([("battery.series_cells", "96.5")], "whole number"),
            ([("production", "2016")], "two comma-separated"),
            ([("model_years", "a,b")], "two years"),
        ],
    )
    def test_bad_input_is_refused(self, prof, pairs, message):
        with pytest.raises(VariantsEditError, match=message):
            edit.add_variant("a1", "A", pairs, profile=prof)

    def test_ids_are_checked_and_unique(self, prof):
        with pytest.raises(VariantsEditError, match="invalid variant ID"):
            edit.add_variant("28", "28", profile=prof)
        edit.add_variant("a1", "A", profile=prof)
        with pytest.raises(VariantsEditError, match="already exists"):
            edit.add_variant("A1", "A again", profile=prof)

    def test_name_cannot_be_removed(self, prof):
        edit.add_variant("a1", "A", profile=prof)
        with pytest.raises(VariantsEditError, match="cannot be removed"):
            edit.unset_variant_meta("a1", ["name"], profile=prof)


class TestCommands:
    def test_list_and_show_json(self, prof, capsys):
        from canlib.commands import variants as cmd

        edit.add_variant("58kwh", "58 kWh", [("battery.gross_kwh", "58")], profile=prof)
        capsys.readouterr()
        assert cmd.cmd_list(argparse.Namespace(json=True)) == 0
        listed = json.loads(capsys.readouterr().out)["variants"]
        assert [r["id"] for r in listed] == ["58kwh"]
        assert cmd.cmd_show(argparse.Namespace(id="58KWH", json=True)) == 0
        assert json.loads(capsys.readouterr().out)["battery"]["gross_kwh"] == 58

    def test_show_unknown(self, prof, capsys):
        from canlib.commands import variants as cmd

        assert cmd.cmd_show(argparse.Namespace(id="nope", json=False)) == 1

    def test_validate_target(self, prof, capsys):
        from canlib.commands.validate import _run_variants

        assert _run_variants() == 0
        edit.add_variant("a1", "A", profile=prof)
        assert _run_variants() == 0
        prof.variants_file.write_text("variants:\n  a1: {colour: red}\n")
        assert _run_variants() == 1


@pytest.mark.parametrize(
    "name",
    [
        "hyundai-ioniq-ae-ev",
        "hyundai-kona-os-ev",
        "hyundai-ioniq-5-ne-ev",
        "nissan-leaf-ze1-ev",
        "xpeng-g6-f30-ev",
    ],
)
def test_every_bundled_profile_declares_valid_variants(name):
    from canlib.profile import resolve_profile

    assert len(v.load_variants(resolve_profile(name))) >= 2


class TestSchemaCompanion:
    """Pin the human-readable schema to the checker's authoritative field lists."""

    def _schema(self) -> dict:
        return yaml.safe_load(SCHEMA.read_text())

    def test_fields_match(self):
        s = self._schema()
        assert (*s["required_variant_fields"], *s["optional_variant_fields"]) == tuple(
            v.VARIANT_FIELDS
        )
        assert tuple(s["battery_fields"]) == tuple(v.BATTERY_FIELDS)
        assert tuple(s["valid_statuses"]) == v.STATUSES
        assert tuple(s["reserved_variant_fields"]) == v.RESERVED_FIELDS

    def test_comment_block_documents_every_field_in_order(self):
        def documented(indent: int) -> list[str]:
            pattern = re.compile(rf"^#{' ' * indent}([a-z_]+):")
            return [
                m.group(1) for line in SCHEMA.read_text().splitlines() if (m := pattern.match(line))
            ]

        assert tuple(documented(7)) == tuple(v.VARIANT_FIELDS)
        assert tuple(documented(9)) == tuple(v.BATTERY_FIELDS)
