"""Tests for choosing a variant, and refusing to talk to an unnamed car.

Plan: ``plans/2026-10-30-profile-variants-4-selection-and-refusal.md``.
"""

from __future__ import annotations

import argparse
import dataclasses

import pytest

from canlib import config
from canlib import profile as profile_mod
from canlib.profile import Profile, ProfileError
from canlib.variants.selection import apply_variant, require_variant
from canlib.variants.tags import capture_tag

VARIANTS = (
    "variants:\n"
    "  28kwh:\n    name: Ioniq 28\n    production: ['2016', '2019-05']\n"
    "  38kwh:\n    name: Ioniq 38\n"
)


@pytest.fixture
def env(tmp_path, monkeypatch):
    """A bundled two-variant profile with an old name, and an empty user config."""
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "cfg"))
    monkeypatch.delenv("CANAIR_PROFILES_DIR", raising=False)
    monkeypatch.delenv("CANAIR_PROFILE", raising=False)
    monkeypatch.delenv("CANAIR_VARIANT", raising=False)
    monkeypatch.setattr("canlib.profile.BUNDLED_PROFILES_DIR", tmp_path / "profiles")
    root = tmp_path / "profiles" / "hyundai-ioniq-ae-ev"
    (root / "ecus").mkdir(parents=True)
    (root / "profile.yaml").write_text(
        'car_model: "x"\ninit: "ATSP6;"\naliases:\n  ioniq-2017: {variant: 28kwh}\n  ioniq-ev: {}\n'
    )
    (root / "variants.yaml").write_text(VARIANTS)
    config.load_config.cache_clear()
    profile_mod._noted_renames.clear()
    yield root
    config.load_config.cache_clear()


def _set(key: str, value: str) -> None:
    config.set_config_key(key, value)
    config.load_config.cache_clear()


class TestPrecedence:
    def _resolve(self, name="hyundai-ioniq-ae-ev", flag=None) -> Profile:
        return apply_variant(profile_mod.resolve_profile(name), flag)

    def test_nothing_named(self, env):
        assert self._resolve().variant is None

    def test_config_remembers_it_per_profile(self, env):
        _set("profile_settings.hyundai-ioniq-ae-ev.variant", "38kwh")
        prof = self._resolve()
        assert (prof.variant, prof.variant_source) == ("38kwh", "config")

    def test_env_beats_config_and_flag_beats_env(self, env, monkeypatch):
        _set("profile_settings.hyundai-ioniq-ae-ev.variant", "38kwh")
        monkeypatch.setenv("CANAIR_VARIANT", "28kwh")
        assert self._resolve().variant_source == "env"
        prof = self._resolve(flag="38kwh")
        assert (prof.variant, prof.variant_source) == ("38kwh", "flag")

    def test_an_old_name_implies_its_variant(self, env):
        prof = self._resolve("ioniq-2017")
        assert (prof.name, prof.variant, prof.variant_source) == (
            "hyundai-ioniq-ae-ev",
            "28kwh",
            "alias",
        )

    def test_an_explicit_choice_beats_the_old_name(self, env):
        _set("profile_settings.hyundai-ioniq-ae-ev.variant", "38kwh")
        assert self._resolve("ioniq-2017").variant == "38kwh"

    def test_an_alias_without_a_variant_implies_none(self, env):
        assert self._resolve("ioniq-ev").variant is None

    def test_the_only_variant_needs_no_choice(self, env):
        (env / "variants.yaml").write_text("variants:\n  28kwh:\n    name: A\n")
        prof = self._resolve()
        assert (prof.variant, prof.variant_source) == ("28kwh", "only")

    def test_a_configured_variant_the_profile_lacks_is_an_error(self, env):
        _set("profile_settings.hyundai-ioniq-ae-ev.variant", "64kwh")
        with pytest.raises(
            ProfileError, match=r"profile_settings.*'64kwh'.*Declared: 28kwh, 38kwh"
        ):
            self._resolve()

    def test_scope_keywords_only_from_the_flag_or_env(self, env):
        assert self._resolve(flag="all").variant == "all"
        _set("profile_settings.hyundai-ioniq-ae-ev.variant", "all")
        with pytest.raises(ProfileError):
            self._resolve()

    def test_set_active_applies_it(self, env):
        _set("profile_settings.hyundai-ioniq-ae-ev.variant", "38kwh")
        assert profile_mod.set_active("hyundai-ioniq-ae-ev").variant == "38kwh"


class TestAttribution:
    @pytest.mark.parametrize(
        ("source", "attribution"),
        [
            ("flag", "selected"),
            ("env", "selected"),
            ("config", "selected"),
            ("alias", "alias"),
            ("only", "only"),
        ],
    )
    def test_each_source_is_recorded(self, source, attribution, tmp_path):
        prof = Profile("x", tmp_path, variant="28kwh", variant_source=source)
        assert capture_tag(prof) == {"variant": "28kwh", "attribution": {"variant": attribution}}


class TestRefusal:
    def test_an_unnamed_car_is_refused_with_how_to_pick(self, env):
        prof = profile_mod.resolve_profile("hyundai-ioniq-ae-ev")
        with pytest.raises(ProfileError) as e:
            require_variant(prof, "talk to the car")
        message = str(e.value)
        assert (
            "describes 2 variants and none is selected, so canair won't talk to the car" in message
        )
        assert "28kwh   Ioniq 28 · built 2016 – 2019-05" in message
        assert "canair profile use hyundai-ioniq-ae-ev --variant 28kwh" in message
        assert "canair --variant 28kwh <command>" in message

    def test_a_named_car_passes(self, env):
        prof = dataclasses.replace(
            profile_mod.resolve_profile("hyundai-ioniq-ae-ev"), variant="38kwh"
        )
        require_variant(prof, "talk to the car")

    def test_a_scope_keyword_is_not_a_car(self, env):
        prof = dataclasses.replace(
            profile_mod.resolve_profile("hyundai-ioniq-ae-ev"), variant="all"
        )
        with pytest.raises(ProfileError, match="scopes analysis"):
            require_variant(prof, "talk to the car")

    def test_a_profile_without_a_choice_passes(self, tmp_path):
        (tmp_path / "ecus").mkdir()
        require_variant(Profile("x", tmp_path), "talk to the car")


class TestLiveGate:
    @pytest.fixture
    def unnamed(self, env, monkeypatch):
        monkeypatch.setattr(
            profile_mod, "_active", profile_mod.resolve_profile("hyundai-ioniq-ae-ev")
        )

    @pytest.mark.parametrize("command", ["read", "monitor", "scan", "raw", "io", "dtc"])
    def test_live_commands_refuse(self, unnamed, command):
        from canlib.commands._live.runtime import _require_car

        with pytest.raises(ProfileError):
            _require_car(argparse.Namespace(command=command, save=False))

    @pytest.mark.parametrize("command", ["discover", "disc", "identity", "id"])
    def test_identifying_commands_run_until_they_save(self, unnamed, command):
        from canlib.commands._live.runtime import _require_car

        _require_car(argparse.Namespace(command=command, save=False))
        with pytest.raises(ProfileError, match="record captures"):
            _require_car(argparse.Namespace(command=command, save=True))

    def test_run_live_refuses_before_touching_the_device(self, unnamed, monkeypatch):
        from canlib.commands._live import runtime

        def _boom(*a, **k):
            raise AssertionError("the device lock must not be taken")

        monkeypatch.setattr(runtime, "WiCANLock", _boom)
        with pytest.raises(ProfileError):
            runtime.run_live(argparse.Namespace(command="read", save=False, force=False))

    def test_autopid_generation_refuses(self, unnamed):
        from canlib.commands import wican

        with pytest.raises(ProfileError, match="generate the AutoPID profile"):
            wican._generate(argparse.Namespace())


class TestProfileUse:
    def _use(self, name, variant=None):
        from canlib.commands.profile import _cmd_use

        return _cmd_use(argparse.Namespace(name=name, use_variant=variant, profiles_dir=None))

    def test_remembers_the_variant(self, env, capsys):
        assert self._use("hyundai-ioniq-ae-ev", "38KWH") == 0
        config.load_config.cache_clear()
        cfg = config.load_config()
        assert cfg["default_profile"] == "hyundai-ioniq-ae-ev"
        assert cfg["profile_settings"]["hyundai-ioniq-ae-ev"]["variant"] == "38kwh"

    def test_an_old_name_is_saved_as_the_current_one(self, env, capsys):
        assert self._use("ioniq-2017", "28kwh") == 0
        config.load_config.cache_clear()
        assert config.load_config()["default_profile"] == "hyundai-ioniq-ae-ev"

    def test_an_undeclared_variant_is_refused(self, env, capsys):
        assert self._use("hyundai-ioniq-ae-ev", "64kwh") == 2
        assert "Declared: 28kwh, 38kwh" in capsys.readouterr().err

    def test_without_a_variant_off_a_terminal_it_says_how(self, env, capsys):
        assert self._use("hyundai-ioniq-ae-ev") == 0
        assert "--variant ID" in capsys.readouterr().out
        config.load_config.cache_clear()
        assert "profile_settings" not in config.load_config()


def test_validate_checks_an_alias_variant(env):
    from canlib.commands.validate.profile_names import profile_name_findings

    text = (env / "profile.yaml").read_text().replace("{variant: 28kwh}", "{variant: 64kwh, x: 1}")
    (env / "profile.yaml").write_text(text)
    errors, _ = profile_name_findings(profile_mod.resolve_profile(str(env)))
    assert any("implies variant '64kwh'" in e for e in errors)
    assert any("unknown field 'x'" in e for e in errors)


def test_the_suite_pins_its_car():
    """conftest names the bundled reference car, as a user does once."""
    assert profile_mod.active().variant == "28kwh"
