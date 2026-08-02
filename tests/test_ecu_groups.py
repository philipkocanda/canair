"""Tests for canlib.ecu_groups — group loading + @group reference expansion."""

from __future__ import annotations

from pathlib import Path

import pytest

from canlib.ecu_groups import (
    Group,
    GroupError,
    expand_group_refs,
    group_members,
    load_groups,
    normalize_group_name,
)


class _Prof:
    def __init__(self, path: Path):
        self.groups_file = path / "groups.yaml"


def _write(prof: _Prof, text: str) -> None:
    prof.groups_file.write_text(text)


# ── loading ──────────────────────────────────────────────────────────────────


class TestLoadGroups:
    def test_absent_file_is_empty(self, tmp_path):
        assert load_groups(_Prof(tmp_path)) == {}

    def test_loads_mapping_form(self, tmp_path):
        prof = _Prof(tmp_path)
        _write(
            prof,
            "groups:\n"
            "  charging:\n"
            "    description: SoC while plugged\n"
            "    members: [BMS:2101, OBC]\n",
        )
        groups = load_groups(prof)
        assert set(groups) == {"charging"}
        assert groups["charging"].members == ("BMS:2101", "OBC")
        assert groups["charging"].description == "SoC while plugged"

    def test_bare_list_shorthand(self, tmp_path):
        prof = _Prof(tmp_path)
        _write(prof, "groups:\n  driving: [BMS, VCU, MCU]\n")
        groups = load_groups(prof)
        assert groups["driving"].members == ("BMS", "VCU", "MCU")
        assert groups["driving"].description == ""

    def test_name_normalized_lowercase(self, tmp_path):
        prof = _Prof(tmp_path)
        _write(prof, "groups:\n  Charging: [BMS]\n")
        assert set(load_groups(prof)) == {"charging"}

    def test_bad_top_level_raises(self, tmp_path):
        prof = _Prof(tmp_path)
        _write(prof, "- not a mapping\n")
        with pytest.raises(GroupError):
            load_groups(prof)

    def test_group_members_unknown_raises(self, tmp_path):
        prof = _Prof(tmp_path)
        _write(prof, "groups:\n  charging: [BMS]\n")
        with pytest.raises(GroupError):
            group_members("nope", profile=prof)


def test_normalize_group_name():
    assert normalize_group_name("  Charging ") == "charging"


# ── expansion (pure) ──────────────────────────────────────────────────────────


@pytest.fixture
def groups() -> dict[str, Group]:
    return {
        "charging": Group("charging", "", ("BMS:2101", "BMS:2105", "OBC")),
        "driving": Group("driving", "", ("BMS", "VCU", "MCU")),
    }


class TestExpandGroupRefs:
    def test_whole_group_ref(self, groups):
        assert expand_group_refs(["@charging"], groups) == ["BMS:2101 BMS:2105 OBC"]

    def test_group_plus_extra_selector_in_one_step(self, groups):
        assert expand_group_refs(["@driving CLU:220B"], groups) == ["BMS VCU MCU CLU:220B"]

    def test_group_and_selector_as_separate_steps(self, groups):
        assert expand_group_refs(["@driving", "BMS:2102"], groups) == ["BMS VCU MCU", "BMS:2102"]

    def test_multiple_groups_in_one_step(self, groups):
        assert expand_group_refs(["@charging @driving"], groups) == [
            "BMS:2101 BMS:2105 OBC BMS VCU MCU"
        ]

    def test_explicit_query_verb_preserved(self, groups):
        assert expand_group_refs(["query @driving"], groups) == ["query BMS VCU MCU"]

    def test_non_query_verb_passthrough(self, groups):
        # A group ref is meaningless in a session/raw/scan step — leave it alone.
        assert expand_group_refs(["session IGPM --wake"], groups) == ["session IGPM --wake"]

    def test_plain_selector_untouched(self, groups):
        assert expand_group_refs(["VCU:2101 BMS:2101"], groups) == ["VCU:2101 BMS:2101"]

    def test_unknown_group_raises(self, groups):
        with pytest.raises(GroupError, match="unknown group @bogus"):
            expand_group_refs(["@bogus"], groups)

    def test_bare_sigil_raises(self, groups):
        with pytest.raises(GroupError):
            expand_group_refs(["@"], groups)

    def test_group_ref_with_pid_raises(self, groups):
        with pytest.raises(GroupError, match="must not carry a PID"):
            expand_group_refs(["@charging:2101"], groups)


def test_non_query_verbs_match_live_step_verbs():
    """Drift guard: our passthrough verb set is the non-query subset of the
    canonical mini-language leading verbs."""
    from canlib.commands._live import STEP_VERBS
    from canlib.ecu_groups import _NON_QUERY_VERBS

    assert _NON_QUERY_VERBS | {"query"} == set(STEP_VERBS)
