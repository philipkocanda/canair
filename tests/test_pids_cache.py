"""Invalidation of caches derived from ECU definitions (canlib.pids)."""

import textwrap

import pytest

from canlib import profile
from canlib.pids import clear_cache, register_derived_cache


def _mk_profile(tmp_path, name, ecu, param):
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
                  parameters:
                    {param}:
                      expression: B04
                      unit: V
            """)
    )
    return root


def _activate(root, name):
    profile._active = profile.Profile(name, root)
    clear_cache()


@pytest.fixture(autouse=True)
def _restore_active():
    saved = profile._active
    yield
    profile._active = saved
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
        from canlib.capture_store import decoded_preview

        entry = {"payload": "6101AABBCCDD", "ecu": "BMS", "pid": "2101"}
        _activate(_mk_profile(tmp_path, "a", "BMS", "A_VOLTS"), "a")
        assert "A_VOLTS" in (decoded_preview(entry) or {})

        _activate(_mk_profile(tmp_path, "b", "BMS", "B_VOLTS"), "b")
        preview = decoded_preview(entry) or {}
        assert "B_VOLTS" in preview, f"stale index decoded {sorted(preview)}"

    def test_editing_a_param_is_reflected(self, tmp_path):
        from canlib.capture_store import decoded_preview
        from canlib.pids_edit import rename_parameter

        root = _mk_profile(tmp_path, "c", "BMS", "OLD_NAME")
        _activate(root, "c")
        entry = {"payload": "6101AABBCCDD", "ecu": "BMS", "pid": "2101"}
        assert "OLD_NAME" in (decoded_preview(entry) or {})

        rename_parameter("BMS", "2101", "OLD_NAME", "NEW_NAME", pids_dir=root / "ecus")
        preview = decoded_preview(entry) or {}
        assert "NEW_NAME" in preview, f"stale index decoded {sorted(preview)}"


class TestLogEcuLookup:
    def test_switching_profile_remaps_tx_ids(self, tmp_path):
        from canlib import log

        _activate(_mk_profile(tmp_path, "la", "BMS", "X"), "la")
        assert log._load_ecu_lookup().get(0x7E4) == "BMS"

        _activate(_mk_profile(tmp_path, "lb", "MCU", "Y"), "lb")
        assert log._load_ecu_lookup().get(0x7E4) == "MCU"


class TestSetActive:
    def test_switching_via_set_active_clears_derived_state(self, tmp_path, monkeypatch):
        calls = []
        _mk_profile(tmp_path, "one", "BMS", "X")
        _mk_profile(tmp_path, "two", "MCU", "Y")
        monkeypatch.setenv("CANAIR_PROFILES_DIR", str(tmp_path))

        profile.set_active("one", profiles_dir=tmp_path)
        register_derived_cache(lambda: calls.append(1))
        profile.set_active("two", profiles_dir=tmp_path)
        assert calls == [1]

    def test_reselecting_the_same_profile_does_not_clear(self, tmp_path):
        calls = []
        _mk_profile(tmp_path, "same", "BMS", "X")
        profile.set_active("same", profiles_dir=tmp_path)
        register_derived_cache(lambda: calls.append(1))
        profile.set_active("same", profiles_dir=tmp_path)
        assert calls == []


class TestMergeEcuDocuments:
    """One ECU per top-level key; a key claimed twice is reported, not silent."""

    def test_merges_across_files(self):
        from pathlib import Path

        from canlib.pids import merge_ecu_documents

        merged = merge_ecu_documents(
            [
                (Path("a.yaml"), {"BMS": {"tx_id": 0x7E4}}),
                (Path("b.yaml"), {"MCU": {"tx_id": 0x7E2}}),
            ]
        )
        assert sorted(merged) == ["BMS", "MCU"]

    def test_empty_and_none_documents_are_skipped(self):
        from pathlib import Path

        from canlib.pids import merge_ecu_documents

        merged = merge_ecu_documents(
            [(Path("a.yaml"), {}), (Path("b.yaml"), None), (Path("c.yaml"), {"BMS": {}})]
        )
        assert list(merged) == ["BMS"]

    def test_duplicate_key_keeps_first_and_warns(self, capsys):
        from pathlib import Path

        from canlib.pids import merge_ecu_documents

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

        from canlib.pids import merge_ecu_documents

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
        from canlib import profile
        from canlib.pids import clear_cache, load_pids

        saved = profile._active
        try:
            for name, ecu in (("one", "BMS"), ("two", "MCU")):
                root = tmp_path / name
                (root / "ecus").mkdir(parents=True)
                (root / "profile.yaml").write_text('car_model: "T"\ninit: "x"\n')
                (root / "ecus" / "e.yaml").write_text(f"{ecu}:\n  tx_id: 0x7E4\n")

            profile._active = profile.Profile("one", tmp_path / "one")
            clear_cache()
            assert list(load_pids()["ecus"]) == ["BMS"]

            profile._active = profile.Profile("two", tmp_path / "two")
            assert profile._active.cache_key == str(tmp_path / "two")
            clear_cache()
            assert list(load_pids()["ecus"]) == ["MCU"]
        finally:
            profile._active = saved
            clear_cache()
