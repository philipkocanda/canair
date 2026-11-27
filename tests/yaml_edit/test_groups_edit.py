"""Tests for canair.yaml_edit.groups — surgical groups.yaml editing."""

from __future__ import annotations

import dataclasses
from pathlib import Path

import pytest

from canair.core import yaml_io
from canair.yaml_edit import groups as groups_edit
from canair.yaml_edit.groups import (
    GroupsEditError,
    add_group,
    normalize_name,
    remove_group,
    rename_group,
    set_group_description,
    set_group_members,
)


class _Prof:
    def __init__(self, path: Path):
        self.groups_file = path / "groups.yaml"


def _groups(prof: _Prof) -> dict:
    return yaml_io.safe_load(prof.groups_file.read_text())["groups"]


class TestNormalizeName:
    def test_lowercases_and_trims(self):
        assert normalize_name("  Charging ") == "charging"

    def test_hyphen_and_digit_ok(self):
        assert normalize_name("charge-detail") == "charge-detail"
        assert normalize_name("12v") == "12v"

    def test_rejects_empty(self):
        with pytest.raises(GroupsEditError):
            normalize_name("  ")

    def test_rejects_space(self):
        with pytest.raises(GroupsEditError):
            normalize_name("my group")


class TestAddGroup:
    def test_scaffolds_file_when_absent(self, tmp_path):
        prof = _Prof(tmp_path)
        add_group("charging", ["BMS:2101", "OBC"], description="SoC", profile=prof)
        assert prof.groups_file.exists()
        g = _groups(prof)
        assert list(g) == ["charging"]
        assert g["charging"]["members"] == ["BMS:2101", "OBC"]
        assert g["charging"]["description"] == "SoC"

    def test_members_flow_styled(self, tmp_path):
        prof = _Prof(tmp_path)
        add_group("driving", ["BMS", "VCU"], profile=prof)
        assert "[BMS, VCU]" in prof.groups_file.read_text()

    def test_rejects_duplicate(self, tmp_path):
        prof = _Prof(tmp_path)
        add_group("charging", ["BMS"], profile=prof)
        with pytest.raises(GroupsEditError):
            add_group("Charging", ["VCU"], profile=prof)

    def test_rejects_empty_members(self, tmp_path):
        prof = _Prof(tmp_path)
        with pytest.raises(GroupsEditError):
            add_group("charging", [], profile=prof)
        assert not prof.groups_file.exists()

    def test_rejects_malformed_selector(self, tmp_path):
        prof = _Prof(tmp_path)
        with pytest.raises(GroupsEditError):
            add_group("charging", ["BMS:2101:extra"], profile=prof)

    def test_rejects_nested_group(self, tmp_path):
        prof = _Prof(tmp_path)
        with pytest.raises(GroupsEditError):
            add_group("charging", ["@driving"], profile=prof)

    def test_preserves_comments(self, tmp_path):
        prof = _Prof(tmp_path)
        prof.groups_file.write_text("# top comment\ngroups:\n  charging: [BMS]\n")
        add_group("driving", ["VCU"], profile=prof)
        assert "# top comment" in prof.groups_file.read_text()
        assert set(_groups(prof)) == {"charging", "driving"}


class TestRemoveGroup:
    def test_removes(self, tmp_path):
        prof = _Prof(tmp_path)
        add_group("charging", ["BMS"], profile=prof)
        add_group("driving", ["VCU"], profile=prof)
        remove_group("Charging", profile=prof)
        assert list(_groups(prof)) == ["driving"]

    def test_missing_raises(self, tmp_path):
        prof = _Prof(tmp_path)
        add_group("charging", ["BMS"], profile=prof)
        with pytest.raises(GroupsEditError):
            remove_group("nope", profile=prof)


class TestRenameGroup:
    def test_renames_preserving_order(self, tmp_path):
        prof = _Prof(tmp_path)
        add_group("a", ["BMS"], profile=prof)
        add_group("body", ["IGPM"], profile=prof)
        add_group("z", ["VCU"], profile=prof)
        rename_group("body", "comfort", profile=prof)
        assert list(_groups(prof)) == ["a", "comfort", "z"]

    def test_collision_raises(self, tmp_path):
        prof = _Prof(tmp_path)
        add_group("a", ["BMS"], profile=prof)
        add_group("b", ["VCU"], profile=prof)
        with pytest.raises(GroupsEditError):
            rename_group("a", "b", profile=prof)


class TestSetFields:
    def test_set_and_clear_description(self, tmp_path):
        prof = _Prof(tmp_path)
        add_group("charging", ["BMS"], profile=prof)
        set_group_description("charging", "SoC while plugged", profile=prof)
        assert _groups(prof)["charging"]["description"] == "SoC while plugged"
        set_group_description("charging", None, profile=prof)
        assert "description" not in _groups(prof)["charging"]

    def test_set_members(self, tmp_path):
        prof = _Prof(tmp_path)
        add_group("charging", ["BMS"], profile=prof)
        set_group_members("charging", ["BMS:2101", "OBC", "VCU"], profile=prof)
        assert _groups(prof)["charging"]["members"] == ["BMS:2101", "OBC", "VCU"]

    def test_set_members_rejects_bad(self, tmp_path):
        prof = _Prof(tmp_path)
        add_group("charging", ["BMS"], profile=prof)
        with pytest.raises(GroupsEditError):
            set_group_members("charging", ["@nested"], profile=prof)


LONG_MEMBERS = [f"ECU{n}:2101" for n in range(14)]


class TestLongMembers:
    def test_wraps_below_the_key_within_100_columns(self, tmp_path):
        prof = _Prof(tmp_path)
        add_group("long", LONG_MEMBERS, profile=prof)
        text = prof.groups_file.read_text()
        assert max(map(len, text.splitlines())) <= 100
        assert "  long:\n    members:\n      [ECU0:2101," in text
        assert _groups(prof)["long"]["members"] == LONG_MEMBERS

    def test_short_list_stays_inline(self, tmp_path):
        prof = _Prof(tmp_path)
        add_group("short", ["BMS:2101", "OBC"], profile=prof)
        assert "    members: [BMS:2101, OBC]\n" in prof.groups_file.read_text()

    def test_editing_another_group_leaves_a_wrapped_list_alone(self, tmp_path):
        prof = _Prof(tmp_path)
        add_group("long", LONG_MEMBERS, profile=prof)
        add_group("short", ["BMS"], profile=prof)
        before = prof.groups_file.read_text()
        set_group_description("short", "Battery", profile=prof)
        after = prof.groups_file.read_text()
        assert after.replace("    description: Battery\n", "") == before


class TestReparseGuard:
    def test_failed_post_check_reverts(self, tmp_path, monkeypatch):
        prof = _Prof(tmp_path)
        add_group("charging", ["BMS"], profile=prof)
        original = prof.groups_file.read_text()

        def _boom(data):
            raise GroupsEditError("simulated invalid file")

        broken = dataclasses.replace(groups_edit.GROUPS, check=_boom)
        monkeypatch.setattr(groups_edit, "GROUPS", broken)
        with pytest.raises(GroupsEditError):
            add_group("driving", ["VCU"], profile=prof)
        assert prof.groups_file.read_text() == original
