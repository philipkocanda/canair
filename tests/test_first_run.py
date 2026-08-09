"""Tests for the first-run profile chooser (canlib.first_run)."""

from __future__ import annotations

import argparse

import pytest

from canlib import first_run, profile
from canlib.pids import clear_cache


@pytest.fixture(autouse=True)
def _restore_active_profile():
    saved = profile._active
    clear_cache()
    yield
    profile._active = saved
    clear_cache()


def _args(**kw):
    base = {"command": "read", "profile": None, "profiles_dir": None, "func": lambda a: 0}
    base.update(kw)
    return argparse.Namespace(**base)


class TestShouldOffer:
    def test_not_offered_when_not_seeded(self, monkeypatch):
        monkeypatch.setattr(first_run, "_is_interactive", lambda: True)
        assert first_run.should_offer(_args(), seeded=False) is False

    def test_not_offered_when_non_interactive(self, monkeypatch):
        monkeypatch.setattr(first_run, "_is_interactive", lambda: False)
        assert first_run.should_offer(_args(), seeded=True) is False

    def test_not_offered_with_explicit_profile(self, monkeypatch):
        monkeypatch.setattr(first_run, "_is_interactive", lambda: True)
        assert first_run.should_offer(_args(profile="x"), seeded=True) is False

    def test_not_offered_with_env_profile(self, monkeypatch):
        monkeypatch.setattr(first_run, "_is_interactive", lambda: True)
        monkeypatch.setenv("CANAIR_PROFILE", "ioniq-2017")
        assert first_run.should_offer(_args(), seeded=True) is False

    @pytest.mark.parametrize("cmd", ["profile", "config", "completion"])
    def test_not_offered_for_self_managing_commands(self, monkeypatch, cmd):
        monkeypatch.setattr(first_run, "_is_interactive", lambda: True)
        monkeypatch.delenv("CANAIR_PROFILE", raising=False)
        assert first_run.should_offer(_args(command=cmd), seeded=True) is False

    def test_not_offered_when_no_func(self, monkeypatch):
        monkeypatch.setattr(first_run, "_is_interactive", lambda: True)
        monkeypatch.delenv("CANAIR_PROFILE", raising=False)
        assert first_run.should_offer(_args(func=None), seeded=True) is False

    def test_offered_when_all_conditions_hold(self, monkeypatch):
        monkeypatch.setattr(first_run, "_is_interactive", lambda: True)
        monkeypatch.delenv("CANAIR_PROFILE", raising=False)
        assert first_run.should_offer(_args(), seeded=True) is True


class TestRunFirstRunSetup:
    def _isolate(self, tmp_path, monkeypatch):
        # Isolate config + profile discovery under tmp_path.
        monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "cfg"))
        monkeypatch.setenv("CANAIR_PROFILES_DIR", str(tmp_path / "profiles"))
        (tmp_path / "profiles").mkdir()
        from canlib import config

        config.load_config.cache_clear()

    def _seed_profile(self, tmp_path, name):
        root = tmp_path / "profiles" / name
        (root / "ecus").mkdir(parents=True)
        (root / "profile.yaml").write_text('car_model: "T"\ninit: "x"\n')

    def test_select_existing_sets_default(self, tmp_path, monkeypatch):
        self._isolate(tmp_path, monkeypatch)
        self._seed_profile(tmp_path, "car-a")
        monkeypatch.setattr(first_run, "_prompt", lambda _t: "1")
        first_run.run_first_run_setup(_args())
        from canlib import config

        config.load_config.cache_clear()
        assert config.get_config_key("default_profile") == "car-a"

    def test_skip_writes_nothing(self, tmp_path, monkeypatch):
        self._isolate(tmp_path, monkeypatch)
        self._seed_profile(tmp_path, "car-a")
        monkeypatch.setattr(first_run, "_prompt", lambda _t: "s")
        first_run.run_first_run_setup(_args())
        from canlib import config

        config.load_config.cache_clear()
        assert config.get_config_key("default_profile") is None

    def test_create_new_profile(self, tmp_path, monkeypatch):
        self._isolate(tmp_path, monkeypatch)
        # Bundled profiles are always discovered, so choose "n" (create), then
        # answer name + car_model.
        answers = iter(["n", "my-car", "VW e-Golf 2019"])
        monkeypatch.setattr(first_run, "_prompt", lambda _t: next(answers))
        first_run.run_first_run_setup(_args())
        from canlib import config

        config.load_config.cache_clear()
        assert config.get_config_key("default_profile") == "my-car"
        assert (tmp_path / "cfg" / "canair" / "profiles" / "my-car" / "profile.yaml").exists()

    def test_empty_new_name_skips(self, tmp_path, monkeypatch):
        self._isolate(tmp_path, monkeypatch)
        # Choose create ("n"), then give an empty name -> skip.
        answers = iter(["n", ""])
        monkeypatch.setattr(first_run, "_prompt", lambda _t: next(answers))
        first_run.run_first_run_setup(_args())
        from canlib import config

        config.load_config.cache_clear()
        assert config.get_config_key("default_profile") is None


class TestSnapshotProfileIsOffered:
    """Choosing a profile that lives in the install snapshot offers a copy first.

    Persisting it as the default otherwise points every later ``--save`` and PID
    edit at ``site-packages``, where the next reinstall deletes them.
    """

    def _isolate(self, tmp_path, monkeypatch):
        monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "cfg"))
        monkeypatch.delenv("CANAIR_PROFILES_DIR", raising=False)
        snapshot = (
            tmp_path
            / "uv"
            / "tools"
            / "canair"
            / "lib"
            / "python3.12"
            / "site-packages"
            / "profiles"
        )
        root = snapshot / "ioniq-2017"
        (root / "ecus").mkdir(parents=True)
        (root / "profile.yaml").write_text('car_model: "T"\ninit: "x"\n')
        monkeypatch.setattr("canlib.profile.BUNDLED_PROFILES_DIR", snapshot)
        from canlib import config

        config.load_config.cache_clear()
        return root

    def test_accepting_adopts_the_profile(self, tmp_path, monkeypatch, capsys):
        self._isolate(tmp_path, monkeypatch)
        answers = iter(["1", "y"])
        monkeypatch.setattr(first_run, "_prompt", lambda _t: next(answers))

        first_run.run_first_run_setup(_args())

        out = capsys.readouterr().out
        assert "uv tool install snapshot" in out
        assert "deleted by the next reinstall" in out
        dest = tmp_path / "cfg" / "canair" / "profiles" / "ioniq-2017"
        assert (dest / "profile.yaml").exists()
        from canlib import config

        config.load_config.cache_clear()
        assert config.get_config_key("default_profile") == "ioniq-2017"

    def test_declining_leaves_it_alone_but_still_sets_the_default(
        self, tmp_path, monkeypatch, capsys
    ):
        self._isolate(tmp_path, monkeypatch)
        answers = iter(["1", "n"])
        monkeypatch.setattr(first_run, "_prompt", lambda _t: next(answers))

        first_run.run_first_run_setup(_args())

        assert "canair profile adopt ioniq-2017" in capsys.readouterr().out
        assert not (tmp_path / "cfg" / "canair" / "profiles" / "ioniq-2017").exists()
        from canlib import config

        config.load_config.cache_clear()
        assert config.get_config_key("default_profile") == "ioniq-2017"
