"""Profile bundle layout — the path properties and how ``meta`` reads them."""

import pytest

from canlib.profile import (
    BUNDLE_MEMBERS,
    Profile,
    member_names,
    members_by_role,
    profile_for_path,
)


def _profile(root) -> Profile:
    return Profile("testcar", root)


class TestBundleLayout:
    """Every member resolves under the profile root at its documented name."""

    def test_member_paths(self, tmp_path):
        prof = _profile(tmp_path)
        assert prof.meta_file == tmp_path / "profile.yaml"
        assert prof.ecus_dir == tmp_path / "ecus"
        assert prof.captures_dir == tmp_path / "captures"
        assert prof.can_dir == tmp_path / "captures" / "can"
        assert prof.can_index_file == tmp_path / "captures" / "can" / "index.yaml"
        assert prof.signals_dir == tmp_path / "signals"
        assert prof.can_buses_file == tmp_path / "can_buses.yaml"
        assert prof.groups_file == tmp_path / "groups.yaml"
        assert prof.references_dir == tmp_path / "references"
        assert prof.dtc_log_file == tmp_path / "dtc_log.yaml"
        assert prof.out_dir == tmp_path / "out"

    def test_states_file_prefers_canonical_name(self, tmp_path):
        # Full fallback behaviour is covered in test_states.py; asserted here so
        # the layout test names every member of the bundle.
        assert _profile(tmp_path).states_file == tmp_path / "vehicle_states.yaml"


class TestMeta:
    def test_reads_the_meta_file(self, tmp_path):
        # meta must resolve through meta_file, not a second hand-built path.
        prof = _profile(tmp_path)
        prof.meta_file.write_text("car_model: Test EV\ninit: ATSP6;\n")
        assert prof.meta["car_model"] == "Test EV"

    def test_absent_meta_file_is_empty(self, tmp_path):
        assert _profile(tmp_path).meta == {}

    def test_empty_meta_file_is_empty(self, tmp_path):
        prof = _profile(tmp_path)
        prof.meta_file.write_text("")
        assert prof.meta == {}


class TestBundleRegistry:
    """The registry is the single declaration of what a profile is made of.

    Anything that reasons about bundle members (`profile show`, `contribute`,
    the blind strip, discovery) reads it, so these guard the invariants those
    consumers rely on — a member added to the registry but not wired to a
    Profile property (or vice versa) is the drift that once let `groups.yaml`
    fall out of contributions.
    """

    def test_every_member_declares_a_profile_property(self, tmp_path):
        prof = _profile(tmp_path)
        for member in BUNDLE_MEMBERS:
            assert member.attr, f"{member.name} has no Profile property"
            assert hasattr(prof, member.attr), f"Profile.{member.attr} missing"
            assert prof.member_path(member).parent == tmp_path

    def test_every_profile_path_property_is_registered(self):
        # can_dir/can_index_file live *inside* captures/, so they are sub-members
        # rather than top-level bundle members.
        sub_members = {"can_dir", "can_index_file"}
        declared = {m.attr for m in BUNDLE_MEMBERS}
        actual = {
            name
            for name in dir(Profile)
            if (name.endswith(("_dir", "_file")) and not name.startswith("_"))
        }
        assert actual - sub_members == declared

    def test_member_path_honours_the_states_fallback(self, tmp_path):
        (states,) = [m for m in BUNDLE_MEMBERS if m.name == "vehicle_states.yaml"]
        (tmp_path / "states.yaml").write_text("states: []\n")
        # Legacy alias present, canonical absent → member_path follows the property.
        assert _profile(tmp_path).member_path(states).name == "states.yaml"

    def test_names_include_legacy_aliases(self):
        names = member_names(members_by_role("definition"))
        assert "vehicle_states.yaml" in names and "states.yaml" in names

    @pytest.mark.parametrize("member", BUNDLE_MEMBERS, ids=lambda m: m.name)
    def test_role_decides_contributability(self, member):
        expected = member.role in ("definition", "evidence")
        assert member.contributable is expected

    def test_required_members_identify_a_profile(self, tmp_path):
        from canlib.profile import looks_like_profile

        assert not looks_like_profile(tmp_path)
        for member in (m for m in BUNDLE_MEMBERS if m.required):
            root = tmp_path / member.name.replace(".", "_")
            root.mkdir()
            path = root / member.name
            path.mkdir() if member.kind == "dir" else path.write_text("")
            assert looks_like_profile(root), f"{member.name} should identify a profile"


class TestProfileForPath:
    """Resolving *which* profile owns a path, without consulting the active one."""

    def _bundle(self, tmp_path, name="car"):
        root = tmp_path / name
        (root / "ecus").mkdir(parents=True)
        (root / "profile.yaml").write_text("car_model: X\n")
        return root

    def test_from_the_root_itself(self, tmp_path):
        root = self._bundle(tmp_path)
        assert profile_for_path(root).root == root

    def test_from_the_ecus_dir(self, tmp_path):
        root = self._bundle(tmp_path)
        prof = profile_for_path(root / "ecus")
        assert (prof.name, prof.root) == (root.name, root)

    def test_from_an_ecu_file(self, tmp_path):
        root = self._bundle(tmp_path)
        (root / "ecus" / "bms.yaml").write_text("BMS:\n  tx_id: 0x7E4\n")
        assert profile_for_path(root / "ecus" / "bms.yaml").root == root

    def test_from_a_deeply_nested_file(self, tmp_path):
        root = self._bundle(tmp_path)
        nested = root / "captures" / "can"
        nested.mkdir(parents=True)
        log = nested / "drive.asc"
        log.write_text("")
        assert profile_for_path(log).root == root

    def test_ignores_the_active_profile(self, tmp_path, monkeypatch):
        from canlib import profile as profile_mod

        other = self._bundle(tmp_path, "other")
        monkeypatch.setattr(profile_mod, "_active", Profile("other", other))
        root = self._bundle(tmp_path, "mine")
        assert profile_for_path(root / "ecus").root == root

    def test_outside_any_bundle_yields_absent_members(self, tmp_path):
        # No bundle anywhere up the tree: the result must not resolve to the
        # active profile — its vocabulary files simply don't exist.
        loose = tmp_path / "scratch" / "ecus"
        loose.mkdir(parents=True)
        prof = profile_for_path(loose)
        assert not prof.states_file.exists()
        assert not prof.can_buses_file.exists()
