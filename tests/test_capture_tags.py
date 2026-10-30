"""Tests for capture variant tags: selection, stamping, scoping, merging and migration.

Plan: ``plans/2026-10-30-profile-variants-3-capture-tags.md``.
"""

from __future__ import annotations

import argparse
import dataclasses
import json
from pathlib import Path

import pytest

from canlib import profile as profile_mod
from canlib.captures import io as capture_io
from canlib.profile import Profile, ProfileError
from canlib.variants import edit as variants_edit
from canlib.variants.selection import apply_variant, require_named_car

VARIANTS = "variants:\n  28kwh:\n    name: A\n  38kwh:\n    name: B\n"


def _session(label: str, *, variant: str | None = None, time: str = "09:00:00") -> dict:
    s: dict = {"date": "2026-01-01", "label": label}
    if variant:
        s["variant"] = variant
        s["attribution"] = {"variant": "selected"}
    s["captures"] = [{"rx": "0x7EC", "pid": "2101", "payload": "6101AA", "time": time}]
    return s


@pytest.fixture
def prof(tmp_path, monkeypatch):
    """A two-variant profile with an empty capture store, made active."""
    monkeypatch.delenv("CANAIR_VARIANT", raising=False)
    root = tmp_path / "kia-ev6-cv-ev"
    (root / "ecus").mkdir(parents=True)
    (root / "captures").mkdir()
    (root / "profile.yaml").write_text('car_model: "x"\ninit: "ATSP6;"\n')
    (root / "ecus" / "bms.yaml").write_text("BMS:\n  tx_id: 0x7E4\n")
    (root / "variants.yaml").write_text(VARIANTS)
    p = Profile(root.name, root)
    monkeypatch.setattr(profile_mod, "_active", p)
    return p


def _select(prof: Profile, variant: str | None, source: str = "flag") -> Profile:
    chosen = dataclasses.replace(prof, variant=variant, variant_source=source if variant else None)
    profile_mod._active = chosen
    return chosen


def _write(path: Path, *sessions: dict) -> Path:
    capture_io.dump_capture_file(path, {"sessions": list(sessions)})
    return path


class TestSelection:
    def test_flag_and_env(self, prof, monkeypatch):
        assert apply_variant(prof, "38KWH").variant == "38kwh"
        monkeypatch.setenv("CANAIR_VARIANT", "28kwh")
        chosen = apply_variant(prof)
        assert (chosen.variant, chosen.variant_source) == ("28kwh", "env")
        assert apply_variant(prof, "38kwh").variant_source == "flag"

    def test_nothing_selected(self, prof):
        assert apply_variant(prof).variant is None

    @pytest.mark.parametrize("scope", ["all", "untagged"])
    def test_scope_keywords(self, prof, scope):
        assert apply_variant(prof, scope).variant == scope

    def test_undeclared_variant_is_an_error_listing_the_valid_ones(self, prof):
        with pytest.raises(ProfileError, match="Declared: 28kwh, 38kwh"):
            apply_variant(prof, "64kwh")

    def test_profile_without_variants(self, prof):
        prof.variants_file.unlink()
        with pytest.raises(ProfileError, match="declares no variants"):
            apply_variant(prof, "28kwh")


class TestStamping:
    def test_save_session_tags_the_selected_variant(self, prof):
        from canlib.captures import build_manual_session, save_session

        _select(prof, "38kwh")
        session = build_manual_session(
            [{"rx": "0x7EC", "pid": "2101", "payload": "6101", "time": "09:00:00"}], label="L"
        )
        path = save_session(session)
        (saved,) = capture_io.load_capture_file(path)["sessions"]
        assert saved["variant"] == "38kwh"
        assert saved["attribution"] == {"variant": "selected"}
        assert list(saved)[-1] == "captures"

    @pytest.mark.parametrize("variant", [None, "all", "untagged"])
    def test_no_car_named_means_no_tag(self, prof, variant):
        from canlib.captures import build_manual_session, save_session

        _select(prof, variant)
        path = save_session(build_manual_session([{"rx": "0x7EC", "pid": "2101"}], label="L"))
        (saved,) = capture_io.load_capture_file(path)["sessions"]
        assert "variant" not in saved

    def test_a_write_to_another_directory_is_not_tagged(self, prof, tmp_path):
        from canlib.captures import build_manual_session, save_session

        _select(prof, "28kwh")
        other = tmp_path / "elsewhere"
        other.mkdir()
        path = save_session(
            build_manual_session([{"rx": "0x7EC", "pid": "2101"}], label="L"), other
        )
        assert "variant" not in capture_io.load_capture_file(path)["sessions"][0]

    def test_a_recovered_journal_keeps_the_car_it_recorded(self, prof):
        from canlib.captures.journal import CaptureJournal, recover

        _select(prof, "38kwh")
        journal = CaptureJournal.open(prof.captures_dir, label="drive")
        journal.append("0x7EC", "2101", "6101AA", time="09:00:00", date="2026-01-01")
        journal.flush()
        journal._close_fh()  # killed before reconcile
        _select(prof, "28kwh")  # a later run selects another car
        written = recover(journal.path)
        assert written is not None
        (saved,) = capture_io.load_capture_file(written)["sessions"]
        assert saved["variant"] == "38kwh"


class TestScope:
    @pytest.fixture
    def store(self, prof):
        _write(
            prof.captures_dir / "2026-01-01.json",
            _session("a", variant="28kwh", time="09:00:00"),
            _session("b", variant="38kwh", time="10:00:00"),
            _session("c", time="11:00:00"),
        )
        return prof

    @pytest.mark.parametrize(
        ("scope", "labels"),
        [(None, {"a", "b", "c"}), ("all", {"a", "b", "c"}), ("28kwh", {"a"}), ("untagged", {"c"})],
    )
    def test_load_all_captures_honours_the_scope(self, store, scope, labels):
        from canlib.captures.store import load_all_captures

        _select(store, scope)
        rows = load_all_captures()
        assert {r["session_label"] for r in rows} == labels
        assert {r["session_variant"] for r in rows} <= {"28kwh", "38kwh", ""}

    def test_an_explicit_directory_is_never_scoped(self, store):
        from canlib.captures.store import load_all_captures

        _select(store, "28kwh")
        assert len(load_all_captures(store.captures_dir)) == 3


class TestTaggedCopyWins:
    def test_layered_read_keeps_the_tagged_copy(self, prof, tmp_path):
        from canlib.captures.store import load_all_captures

        overlay = tmp_path / "overlay"
        (overlay / "captures").mkdir(parents=True)
        _write(prof.captures_dir / "2026-01-01.json", _session("a", variant="28kwh"))
        _write(overlay / "captures" / "2026-01-01.json", _session("a"))
        profile_mod._active = dataclasses.replace(prof, overlays=(overlay,))
        (row,) = load_all_captures()
        assert row["session_variant"] == "28kwh"

    def test_merge_driver_keeps_one_tagged_copy(self):
        from canlib.captures.merge import merge_documents

        merged = merge_documents(
            {"sessions": []},
            {"sessions": [_session("a", variant="28kwh")]},
            {"sessions": [_session("a")]},
        )
        assert [s.get("variant") for s in merged["sessions"]] == ["28kwh"]

    def test_merge_driver_accepts_a_migration_on_one_side(self):
        from canlib.captures.merge import merge_documents

        base = {"sessions": [_session("a")]}
        merged = merge_documents(base, {"sessions": [_session("a", variant="28kwh")]}, base)
        assert [s.get("variant") for s in merged["sessions"]] == ["28kwh"]

    def test_overlay_never_strips_an_upstream_tag(self):
        from canlib.captures.merge import overlay

        result = overlay(
            {"sessions": [_session("a", variant="28kwh")]}, {"sessions": [_session("a")]}
        )
        assert result.doc is None

    def test_overlay_still_contributes_a_tag(self):
        from canlib.captures.merge import overlay

        result = overlay(
            {"sessions": [_session("a")]}, {"sessions": [_session("a", variant="28kwh")]}
        )
        assert result.doc is not None
        assert result.doc["sessions"][0]["variant"] == "28kwh"
        assert [r.fields for r in result.replaced] == [["attribution", "variant"]]


class TestMigrateVariants:
    def _run(self, **kw):
        from canlib.commands.captures.migrate_variants import cmd_migrate_variants

        base = {"id": "28kwh", "date": None, "since": None, "until": None, "label": None}
        base.update(dry_run=False, json=True, dir=None)
        base.update(kw)
        return cmd_migrate_variants(argparse.Namespace(**base))

    def test_tags_only_untagged_sessions_in_scope(self, prof, capsys):
        path = _write(
            prof.captures_dir / "2026-01-01.json",
            _session("commute"),
            _session("trip", variant="38kwh"),
            _session("other"),
        )
        assert self._run(label="comm") == 0
        assert json.loads(capsys.readouterr().out)["sessions"] == 1
        sessions = capture_io.load_capture_file(path)["sessions"]
        assert [s.get("variant") for s in sessions] == ["28kwh", "38kwh", None]
        assert sessions[0]["attribution"] == {"variant": "migrated"}
        assert sessions[1]["attribution"] == {"variant": "selected"}

    def test_dry_run_and_idempotence(self, prof, capsys):
        path = _write(prof.captures_dir / "2026-01-01.json", _session("a"))
        before = path.read_text()
        self._run(dry_run=True)
        assert path.read_text() == before
        self._run()
        capsys.readouterr()
        self._run()
        assert json.loads(capsys.readouterr().out)["sessions"] == 0  # nothing left to tag
        assert capture_io.load_capture_file(path)["sessions"][0]["variant"] == "28kwh"

    def test_an_undeclared_variant_is_refused(self, prof):
        assert self._run(id="64kwh") == 2


class TestSetVariant:
    def test_asserts_and_clears(self, prof):
        from canlib.captures import set_session_variant

        path = _write(prof.captures_dir / "2026-01-01.json", _session("a", variant="28kwh"))
        set_session_variant(path, 0, "38kwh")
        session = capture_io.load_capture_file(path)["sessions"][0]
        assert (session["variant"], session["attribution"]) == ("38kwh", {"variant": "asserted"})
        set_session_variant(path, 0, "")
        session = capture_io.load_capture_file(path)["sessions"][0]
        assert "variant" not in session and "attribution" not in session

    def test_an_undeclared_variant_is_refused(self, prof, capsys):
        from canlib.captures.store import load_all_captures
        from canlib.commands.captures.session_fields import cmd_set_variant

        _write(prof.captures_dir / "2026-01-01.json", _session("a"))
        assert cmd_set_variant(load_all_captures(), "64kwh", assume_yes=True) == 2


class TestImportsNameTheCar:
    def test_a_multi_variant_profile_needs_the_flag(self, prof, monkeypatch):
        with pytest.raises(ProfileError, match="--variant ID import uds"):
            require_named_car(prof, "import uds")
        monkeypatch.setenv("CANAIR_VARIANT", "28kwh")
        with pytest.raises(ProfileError):
            require_named_car(apply_variant(prof), "import uds")  # an env default is not enough
        assert require_named_car(_select(prof, "38kwh"), "import uds") == "38kwh"

    def test_a_single_variant_profile_needs_nothing(self, prof):
        prof.variants_file.write_text("variants:\n  28kwh:\n    name: A\n")
        assert require_named_car(prof, "import uds") is None


class TestRenameAndRemove:
    def test_rename_retags_history(self, prof):
        from canlib.variants.references import find_references, rename_everywhere

        path = _write(
            prof.captures_dir / "2026-01-01.json", _session("a", variant="28kwh"), _session("b")
        )
        prof.can_dir.mkdir(parents=True)
        prof.can_index_file.write_text("logs:\n- file: x.asc\n  format: asc\n  variant: 28kwh\n")
        prof.dtc_log_file.write_text("scans:\n- timestamp: t\n  variant: 28kwh\n")
        assert find_references(prof, "28kwh").total == 3
        _, written = rename_everywhere("28kwh", "28kwh-eu", prof)
        assert len(written) == 3
        assert capture_io.load_capture_file(path)["sessions"][0]["variant"] == "28kwh-eu"
        assert "variant: 28kwh-eu" in prof.can_index_file.read_text()
        assert "variant: 28kwh-eu" in prof.dtc_log_file.read_text()
        assert "28kwh-eu" in prof.variants_file.read_text()
        assert find_references(prof, "28kwh").total == 0

    def test_remove_is_refused_while_referenced(self, prof):
        from canlib.variants.edit import VariantsEditError
        from canlib.variants.references import remove_unreferenced

        _write(prof.captures_dir / "2026-01-01.json", _session("a", variant="38kwh"))
        with pytest.raises(VariantsEditError, match="still used by 1 capture session"):
            remove_unreferenced("38kwh", prof)
        remove_unreferenced("28kwh", prof)
        assert "28kwh" not in prof.variants_file.read_text()


class TestValidation:
    def test_untagged_and_undeclared_tags_are_reported(self, prof, capsys):
        from canlib.commands.validate.variant_tags import report_variant_tags

        path = _write(
            prof.captures_dir / "2026-01-01.json",
            _session("a"),
            _session("b", variant="64kwh"),
            _session("c", variant="28kwh"),
        )
        assert report_variant_tags([path], prof) == 1
        out = capsys.readouterr().out
        assert "'64kwh', which the profile does not declare" in out
        assert "1 untagged session(s)" in out

    def test_untagged_sessions_are_not_flagged_on_a_single_variant_profile(self, prof, capsys):
        from canlib.commands.validate.variant_tags import report_variant_tags

        variants_edit.remove_variant("38kwh", profile=prof)
        path = _write(prof.captures_dir / "2026-01-01.json", _session("a"))
        assert report_variant_tags([path], prof) == 0
        assert "untagged" not in capsys.readouterr().out


def test_sessions_view_names_each_car(prof, capsys):
    from canlib.captures.store import load_all_captures
    from canlib.commands.captures.sessions import cmd_sessions

    _write(prof.captures_dir / "2026-01-01.json", _session("a", variant="28kwh"), _session("b"))
    cmd_sessions(load_all_captures())
    out = capsys.readouterr().out
    assert "28kwh" in out and "untagged" in out
    cmd_sessions(load_all_captures(), as_json=True)
    assert [s["variant"] for s in json.loads(capsys.readouterr().out)] == ["28kwh", None]


def test_bundled_history_is_tagged_where_the_car_is_known():
    """Ioniq, Kona and XPeng captures came from one known car each; all are tagged."""
    from canlib.commands.validate.variant_tags import variant_tag_findings
    from canlib.profile import resolve_profile
    from canlib.variants import load_variants

    for name in ("hyundai-ioniq-ae-ev", "hyundai-kona-os-ev", "xpeng-g6-f30-ev"):
        prof = resolve_profile(name)
        files = capture_io.iter_capture_files(prof.captures_dir)
        untagged, undeclared = variant_tag_findings(files, set(load_variants(prof)))
        assert (untagged, dict(undeclared)) == (0, {}), name
