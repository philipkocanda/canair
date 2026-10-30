"""Tests for renamed profiles: aliases keep an old name resolving to its successor.

A bundled profile renamed to the make-model-code-drivetrain convention declares its
old name under ``aliases:`` in ``profile.yaml``. Every way a user named the profile —
``--profile``, ``CANAIR_PROFILE``, ``default_profile``, a capture overlay filed under
the old name — must keep landing on the renamed profile, while a real directory of the
old name (an adopted copy) is never hijacked.

Plan: ``plans/2026-10-30-profile-variants-1-rename-and-aliases.md``.
"""

from __future__ import annotations

import argparse
import dataclasses
import json
from pathlib import Path

import pytest

from canlib.captures.store import load_all_captures
from canlib.commands.profile import _cmd_list, _cmd_use
from canlib.commands.validate.profile_names import profile_name_findings
from canlib.profile import ProfileError, alias_index, resolve_profile, selectable_profiles
from canlib.profile_names import build_alias_index, convention_problem

OLD, NEW = "ev6-2022", "kia-ev6-cv-ev"


@pytest.fixture(autouse=True)
def _isolate_profile_state():
    from canlib import config, profile

    profile._active = None
    profile._noted_renames.clear()
    config.load_config.cache_clear()
    yield
    profile._active = None
    profile._noted_renames.clear()
    config.load_config.cache_clear()


@pytest.fixture
def env(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "cfg"))
    monkeypatch.delenv("CANAIR_PROFILES_DIR", raising=False)
    monkeypatch.delenv("CANAIR_PROFILE", raising=False)
    monkeypatch.setattr("canlib.profile.BUNDLED_PROFILES_DIR", tmp_path / "profiles")
    monkeypatch.setattr(
        "canlib.commands.validate.profile_names.BUNDLED_PROFILES_DIR", tmp_path / "profiles"
    )
    return argparse.Namespace(
        bundled=tmp_path / "profiles",
        user=tmp_path / "cfg" / "canair" / "profiles",
        config=tmp_path / "cfg" / "canair" / "config.yaml",
    )


def _bundle(root: Path, *, aliases: tuple[str, ...] = (), extends: str | None = None) -> Path:
    (root / "ecus").mkdir(parents=True)
    (root / "captures").mkdir()
    lines = [] if extends else [f'car_model: "{root.name}"', 'init: "ATSP6;"']
    if extends:
        lines.append(f"extends: {extends}")
    if aliases:
        lines.append("aliases:")
        lines += [f"  {a}: {{}}" for a in aliases]
    (root / "profile.yaml").write_text("\n".join(lines) + "\n")
    return root


def _renamed(env) -> Path:
    return _bundle(env.bundled / NEW, aliases=(OLD,))


def _seed(path: Path, label: str) -> None:
    session = {
        "date": "2026-01-01",
        "label": label,
        "captures": [{"rx": "0x7EC", "pid": "2101", "payload": "6101AA", "time": "09:00:00"}],
    }
    path.write_text(json.dumps({"sessions": [session]}))


class TestConvention:
    @pytest.mark.parametrize(
        "name",
        ["hyundai-ioniq-ae-ev", "hyundai-ioniq-5-ne-ev", "xpeng-g6-f30-erev", "kia-niro-de-hev"],
    )
    def test_conforming_names(self, name):
        assert convention_problem(name) is None

    @pytest.mark.parametrize(
        "name", ["ioniq-2017", "hyundai-ioniq-ev", "hyundai-ioniq-ae", "Hyundai-Ioniq-AE-EV"]
    )
    def test_names_missing_a_part_or_using_a_year(self, name):
        assert convention_problem(name) is not None


class TestAliasIndex:
    def test_maps_old_names_and_flags_conflicts(self, tmp_path):
        a = _bundle(tmp_path / "a", aliases=("old", "shared"))
        b = _bundle(tmp_path / "b", aliases=("shared",))
        index = build_alias_index({"a": a, "b": b})
        assert index.target("old") == "a"
        assert index.target("shared") is None
        assert index.conflicts["shared"] == ("a", "b")
        assert index.aliases_of("a") == ["old"]

    def test_a_profile_cannot_alias_itself(self, tmp_path):
        a = _bundle(tmp_path / "a", aliases=("a",))
        assert build_alias_index({"a": a}).targets == {}


class TestResolution:
    def test_old_name_resolves_to_the_renamed_profile_with_a_note(self, env, capsys):
        root = _renamed(env)
        prof = resolve_profile(OLD)
        assert (prof.name, prof.root) == (NEW, root)
        assert f"'{OLD}' is now '{NEW}'" in capsys.readouterr().err

    def test_note_is_printed_once_per_process(self, env, capsys):
        _renamed(env)
        resolve_profile(OLD)
        resolve_profile(OLD)
        assert capsys.readouterr().err.count("is now") == 1

    def test_env_var_old_name_resolves(self, env, monkeypatch):
        _renamed(env)
        monkeypatch.setenv("CANAIR_PROFILE", OLD)
        assert resolve_profile().name == NEW

    def test_default_profile_old_name_resolves_silently(self, env, capsys):
        # A config shared by checkouts of different ages keeps the old name, so it
        # must keep working without nagging on every command.
        _renamed(env)
        env.config.parent.mkdir(parents=True)
        env.config.write_text(f"default_profile: {OLD}\n")
        assert resolve_profile().name == NEW
        assert capsys.readouterr().err == ""

    def test_a_real_directory_of_the_old_name_wins(self, env):
        _renamed(env)
        adopted = _bundle(env.user / OLD)
        prof = resolve_profile(OLD)
        assert (prof.name, prof.root) == (OLD, adopted)

    def test_ambiguous_alias_is_an_error(self, env):
        _bundle(env.bundled / NEW, aliases=(OLD,))
        _bundle(env.bundled / "kia-ev6-cv-phev", aliases=(OLD,))
        with pytest.raises(ProfileError, match="more than one profile"):
            resolve_profile(OLD)

    def test_unknown_name_still_errors(self, env):
        _renamed(env)
        with pytest.raises(ProfileError, match="not found"):
            resolve_profile("no-such-car")


class TestOldNamedOverlay:
    """A capture overlay created before the rename (``extends: <old name>``)."""

    def test_layers_onto_the_renamed_profile(self, env):
        root = _renamed(env)
        overlay = _bundle(env.user / OLD, extends=OLD)
        prof = resolve_profile(NEW)
        assert (prof.name, prof.root, prof.overlays) == (NEW, root, (overlay,))
        assert prof.write_root == overlay

    def test_its_captures_are_read_with_the_base(self, env):
        root = _renamed(env)
        overlay = _bundle(env.user / OLD, extends=OLD)
        _seed(root / "captures" / "2026-01-01.json", "upstream")
        _seed(overlay / "captures" / "2026-01-02.json", "mine")
        from canlib import profile as profile_mod

        profile_mod._active = resolve_profile(NEW)
        assert {c["session_label"] for c in load_all_captures()} == {"upstream", "mine"}

    def test_the_old_name_resolves_through_it(self, env):
        _renamed(env)
        overlay = _bundle(env.user / OLD, extends=OLD)
        prof = resolve_profile(OLD)
        assert (prof.name, prof.overlays) == (NEW, (overlay,))

    def test_is_not_listed_as_a_profile(self, env):
        _renamed(env)
        _bundle(env.user / OLD, extends=OLD)
        assert list(selectable_profiles()) == [NEW]

    def test_single_profile_auto_selection_ignores_it(self, env):
        _renamed(env)
        _bundle(env.user / OLD, extends=OLD)
        assert resolve_profile().name == NEW


class TestCommands:
    def test_list_shows_renames(self, env, capsys):
        _renamed(env)
        assert _cmd_list(argparse.Namespace(profiles_dir=None)) == 0
        out = capsys.readouterr().out
        assert f"{OLD} → {NEW}" in out

    def test_use_writes_the_current_name(self, env, capsys):
        _renamed(env)
        assert _cmd_use(argparse.Namespace(name=OLD, profiles_dir=None)) == 0
        assert f"default_profile: {NEW}" in env.config.read_text()


class TestValidation:
    def _profile(self, root: Path):
        return resolve_profile(str(root))

    def test_bundled_name_must_follow_the_convention(self, env):
        root = _bundle(env.bundled / "ev6-2022")
        errors, _ = profile_name_findings(self._profile(root))
        assert any("naming convention" in e for e in errors)

    def test_user_profile_name_is_not_policed(self, env):
        root = _bundle(env.user / "my-car")
        errors, _ = profile_name_findings(self._profile(root))
        assert errors == []

    def test_conforming_bundle_with_aliases_is_clean(self, env):
        root = _renamed(env)
        assert profile_name_findings(self._profile(root)) == ([], [])

    def test_alias_claimed_twice_is_an_error(self, env):
        root = _renamed(env)
        _bundle(env.bundled / "kia-ev6-cv-phev", aliases=(OLD,))
        errors, _ = profile_name_findings(self._profile(root))
        assert any("also declared by kia-ev6-cv-phev" in e for e in errors)

    def test_alias_shadowed_by_a_real_directory_is_a_warning(self, env):
        root = _renamed(env)
        _bundle(env.user / OLD)
        errors, warnings = profile_name_findings(self._profile(root))
        assert errors == []
        assert any("shadowed" in w for w in warnings)

    def test_aliases_must_be_a_mapping(self, env):
        root = _renamed(env)
        (root / "profile.yaml").write_text(f'car_model: "x"\ninit: "ATSP6;"\naliases: [{OLD}]\n')
        errors, _ = profile_name_findings(self._profile(root))
        assert any("must be a mapping" in e for e in errors)


class TestContribute:
    def test_an_old_named_copy_is_contributed_under_the_current_name(self, env):
        from canlib.commands import contribute as cmd

        _renamed(env)
        adopted = _bundle(env.user / OLD)
        prof = resolve_profile(OLD)

        class _Rep:
            json_mode = True

            def __init__(self):
                self.notes: dict = {}

            def note(self, **fields):
                self.notes.update(fields)

        rep = _Rep()
        upstream = cmd._upstream_named(prof, rep)
        assert (upstream.name, upstream.root) == (NEW, adopted)
        assert rep.notes == {"renamed_from": OLD}

    def test_a_current_name_is_left_alone(self, env):
        from canlib.commands import contribute as cmd

        root = _renamed(env)
        prof = resolve_profile(NEW)
        assert cmd._upstream_named(prof, None) == dataclasses.replace(prof, root=root)


def test_alias_index_reads_the_base_not_an_overlay(env):
    _renamed(env)
    _bundle(env.user / NEW, extends=NEW)
    assert alias_index().target(OLD) == NEW


@pytest.mark.parametrize(
    ("old", "new"),
    [
        ("ioniq-2017", "hyundai-ioniq-ae-ev"),
        ("kona-electric-2019", "hyundai-kona-os-ev"),
        ("ioniq-5-2022", "hyundai-ioniq-5-ne-ev"),
        ("nissan-leaf-ze1", "nissan-leaf-ze1-ev"),
        ("xpeng-g6", "xpeng-g6-f30-ev"),
    ],
)
def test_every_bundled_profile_keeps_its_old_name(old, new):
    """The renamed bundled profiles resolve from the names users already have."""
    assert resolve_profile(old).name == new
