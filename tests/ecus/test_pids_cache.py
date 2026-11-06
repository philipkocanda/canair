"""Invalidation of caches derived from ECU definitions (canair.ecus.pids)."""

import textwrap

import pytest

from canair.cli import profile_context
from canair.cli.profile_context import active
from canair.ecus.pids import clear_cache, register_derived_cache
from canair.profile import model as profile_model


def _mk_profile(tmp_path, name, ecu, param, sensitive=False):
    root = tmp_path / name
    (root / "ecus").mkdir(parents=True)
    (root / "profile.yaml").write_text('car_model: "T"\ninit: "ATSP6;"\n')
    (root / "ecus" / "e.yaml").write_text(
        textwrap.dedent(f"""\
            {ecu}:
              tx_id: 0x7E4
              pids:
                2101:
                  status: active
                  sensitive: {str(sensitive).lower()}
                  parameters:
                    {param}:
                      expression: B04
                      unit: V
            """)
    )
    return root


def _activate(root, name):
    profile_context.replace_active(profile_model.Profile(name, root))


@pytest.fixture(autouse=True)
def _restore_active():
    saved = profile_context._active
    yield
    profile_context._active = saved
    clear_cache()


class TestDerivedCacheRegistry:
    def test_clear_cache_runs_registered_hooks(self):
        calls = []
        register_derived_cache(lambda: calls.append(1))
        clear_cache()
        assert calls == [1]

    def test_registration_is_idempotent(self):
        calls = []

        def hook():
            calls.append(1)

        register_derived_cache(hook)
        register_derived_cache(hook)
        clear_cache()
        # Registered twice, cleared once — a lazily-built cache re-registers on
        # every rebuild and must not accumulate hooks.
        assert calls == [1]


class TestDecodePreviewIndex:
    """The capture-preview PID index is built from load_pids() — derived state."""

    def test_switching_profile_redecodes_with_the_new_definitions(self, tmp_path):
        from canair.captures.store import decoded_preview

        entry = {"payload": "6101AABBCCDD", "ecu": "BMS", "pid": "2101"}
        _activate(_mk_profile(tmp_path, "a", "BMS", "A_VOLTS"), "a")
        assert "A_VOLTS" in (decoded_preview(entry, profile=active()) or {})

        _activate(_mk_profile(tmp_path, "b", "BMS", "B_VOLTS"), "b")
        preview = decoded_preview(entry, profile=active()) or {}
        assert "B_VOLTS" in preview, f"stale index decoded {sorted(preview)}"

    def test_editing_a_param_is_reflected(self, tmp_path):
        from canair.captures.store import decoded_preview
        from canair.yaml_edit.pids import rename_parameter

        root = _mk_profile(tmp_path, "c", "BMS", "OLD_NAME")
        _activate(root, "c")
        entry = {"payload": "6101AABBCCDD", "ecu": "BMS", "pid": "2101"}
        assert "OLD_NAME" in (decoded_preview(entry, profile=active()) or {})

        rename_parameter(
            "BMS", "2101", "OLD_NAME", "NEW_NAME", pids_dir=root / "ecus", profile=active()
        )
        preview = decoded_preview(entry, profile=active()) or {}
        assert "NEW_NAME" in preview, f"stale index decoded {sorted(preview)}"


class TestSwitchingProfiles:
    """Two profiles in one process need no cache clearing: every cache is keyed by the profile."""

    def test_each_profile_reads_its_own_definitions_decode_index_and_sensitive_set(self, tmp_path):
        from canair.captures.store import decoded_preview
        from canair.ecus.pids import load_pids
        from canair.privacy.sensitive import profile_set
        from canair.profile.model import Profile

        one = Profile("one", _mk_profile(tmp_path, "one", "BMS", "ONE_VOLTS", sensitive=True))
        two = Profile("two", _mk_profile(tmp_path, "two", "MCU", "TWO_VOLTS"))
        entry = {"payload": "6101AABBCCDD", "ecu": "BMS", "pid": "2101"}

        def read(prof):
            ecu = sorted(load_pids(prof)["ecus"])[0]
            preview = decoded_preview(dict(entry, ecu=ecu), profile=prof)
            return (
                sorted(load_pids(prof)["ecus"]),
                sorted(preview or {}),
                profile_set(prof).has("BMS", "2101"),
            )

        first = read(one)
        second = read(two)
        assert read(one) == first

        assert first == (["BMS"], ["ONE_VOLTS"], True)
        assert second == (["MCU"], ["TWO_VOLTS"], False)

    def test_switching_does_not_clear_the_caches(self, tmp_path):
        from canair.ecus.pids import load_pids
        from canair.profile.model import Profile

        calls = []
        one = Profile("one", _mk_profile(tmp_path, "one", "BMS", "X"))
        two = Profile("two", _mk_profile(tmp_path, "two", "MCU", "Y"))

        load_pids(one)
        register_derived_cache(lambda: calls.append(1))
        load_pids(two)
        assert calls == []


class TestMergeEcuDocuments:
    """One ECU per top-level key; a key claimed twice is reported, not silent."""

    def test_merges_across_files(self):
        from pathlib import Path

        from canair.ecus.pids import merge_ecu_documents

        merged = merge_ecu_documents(
            [
                (Path("a.yaml"), {"BMS": {"tx_id": 0x7E4}}),
                (Path("b.yaml"), {"MCU": {"tx_id": 0x7E2}}),
            ]
        )
        assert sorted(merged) == ["BMS", "MCU"]

    def test_empty_and_none_documents_are_skipped(self):
        from pathlib import Path

        from canair.ecus.pids import merge_ecu_documents

        merged = merge_ecu_documents(
            [(Path("a.yaml"), {}), (Path("b.yaml"), None), (Path("c.yaml"), {"BMS": {}})]
        )
        assert list(merged) == ["BMS"]

    def test_duplicate_key_keeps_first_and_warns(self, capsys):
        from pathlib import Path

        from canair.ecus.pids import merge_ecu_documents

        merged = merge_ecu_documents(
            [
                (Path("first.yaml"), {"BMS": {"tx_id": 0x7E4}}),
                (Path("second.yaml"), {"BMS": {"tx_id": 0x111}}),
            ]
        )
        # Deterministic: the first file wins, rather than file order deciding
        # silently as the previous dict.update() did.
        assert merged["BMS"]["tx_id"] == 0x7E4
        err = capsys.readouterr().err
        assert "BMS" in err and "first.yaml" in err and "second.yaml" in err

    def test_non_mapping_document_is_skipped_with_a_warning(self, capsys):
        from pathlib import Path

        from canair.ecus.pids import merge_ecu_documents

        # Every reader of ecus/ funnels through here, so a stray scalar must not
        # raise — that turns one malformed file into an opaque AttributeError in
        # whatever command happened to load the profile.
        merged = merge_ecu_documents(
            [
                (Path("junk.yaml"), "x"),
                (Path("list.yaml"), ["BMS"]),
                (Path("ok.yaml"), {"BMS": {"tx_id": 0x7E4}}),
            ]
        )
        assert list(merged) == ["BMS"]
        err = capsys.readouterr().err
        assert "junk.yaml" in err and "str" in err
        assert "list.yaml" in err and "list" in err


class TestLoadPidsCacheKey:
    def test_two_profiles_do_not_share_a_cache_entry(self, tmp_path):
        from canair.ecus.pids import clear_cache, load_pids
        from canair.profile import model as profile_model

        saved = profile_context._active
        try:
            for name, ecu in (("one", "BMS"), ("two", "MCU")):
                root = tmp_path / name
                (root / "ecus").mkdir(parents=True)
                (root / "profile.yaml").write_text('car_model: "T"\ninit: "x"\n')
                (root / "ecus" / "e.yaml").write_text(f"{ecu}:\n  tx_id: 0x7E4\n")

            profile_context._active = profile_model.Profile("one", tmp_path / "one")
            clear_cache()
            assert list(load_pids(profile=active())["ecus"]) == ["BMS"]

            profile_context._active = profile_model.Profile("two", tmp_path / "two")
            assert profile_context._active.cache_key == str(tmp_path / "two")
            clear_cache()
            assert list(load_pids(profile=active())["ecus"]) == ["MCU"]
        finally:
            profile_context._active = saved
            clear_cache()
