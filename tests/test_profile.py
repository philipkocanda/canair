"""Profile bundle layout — the path properties and how ``meta`` reads them."""

from canlib.profile import Profile


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
