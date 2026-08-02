"""Tests for canlib.groups_edit — surgical groups.yaml editing."""

from __future__ import annotations

from pathlib import Path

import pytest

from canlib import groups_edit, yaml_io
from canlib.groups_edit import (
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


class TestReparseGuard:
    def test_failed_post_check_reverts(self, tmp_path, monkeypatch):
        prof = _Prof(tmp_path)
        add_group("charging", ["BMS"], profile=prof)
        original = prof.groups_file.read_text()

        def _boom(path):
            raise GroupsEditError("simulated invalid file")

        monkeypatch.setattr(groups_edit, "_reparse_validate", _boom)
        with pytest.raises(GroupsEditError):
            add_group("driving", ["VCU"], profile=prof)
        assert prof.groups_file.read_text() == original
