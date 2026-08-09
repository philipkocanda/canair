"""Tests for `canair profile create` scaffolding and config.set_config_value."""

from __future__ import annotations

import argparse

import pytest
import yaml

from canlib.commands.profile import (
    _cmd_adopt,
    _cmd_create,
    _cmd_default,
    _cmd_list,
    _cmd_show,
    _cmd_use,
)
from canlib.commands.validate import collect_pids_validation
from canlib.profile_create import DEFAULT_INIT


@pytest.fixture(autouse=True)
def _isolate_profile_state():
    """Reset the memoized active profile and config cache around each test.

    `_cmd_list`/`active()` memoize a Profile into a module global; when the test
    points it at a tmp_path that's later deleted, it would pollute unrelated
    tests that resolve the active profile.
    """
    from canlib import config, profile

    profile._active = None
    config.load_config.cache_clear()
    yield
    profile._active = None
    config.load_config.cache_clear()


def _args(**kw) -> argparse.Namespace:
    base = {
        "name": "testcar",
        "car_model": "VW e-Golf 2019",
        "init": None,
        "path": None,
        "set_default": False,
        "force": False,
    }
    base.update(kw)
    return argparse.Namespace(**base)


class TestProfileCreate:
    def test_scaffolds_bundle(self, tmp_path):
        root = tmp_path / "prof"
        rc = _cmd_create(_args(path=root))
        assert rc == 0
        assert (root / "ecus").is_dir()
        assert (root / "captures").is_dir()
        assert (root / "out").is_dir()
        assert (root / "profile.yaml").exists()

    def test_scaffolds_can_buses(self, tmp_path):
        root = tmp_path / "prof"
        _cmd_create(_args(path=root))
        cb = root / "can_buses.yaml"
        assert cb.exists()
        data = yaml.safe_load(cb.read_text())
        assert "ALL" in data["can_buses"]
        assert data["can_buses"]["ALL"]["name"]

    def test_scaffolds_vehicle_states(self, tmp_path):
        root = tmp_path / "prof"
        _cmd_create(_args(path=root))
        vs = root / "vehicle_states.yaml"
        assert vs.exists()
        assert not (root / "states.yaml").exists()
        data = yaml.safe_load(vs.read_text())
        names = {s["name"] for s in data["states"]}
        # Scaffold declares the make-neutral base ladder (EV states are commented).
        assert "RUN" in names
        assert {"SLEEP", "ACC", "CRANK", "ALL"} <= names

    def test_scaffolds_groups(self, tmp_path):
        root = tmp_path / "prof"
        _cmd_create(_args(path=root))
        gf = root / "groups.yaml"
        assert gf.exists()
        # Scaffold ships an empty groups mapping (examples commented out).
        assert yaml.safe_load(gf.read_text()) == {"groups": {}}

    def test_meta_contents(self, tmp_path):
        root = tmp_path / "prof"
        _cmd_create(_args(path=root, init="ATSP0;"))
        meta = yaml.safe_load((root / "profile.yaml").read_text())
        assert meta["car_model"] == "VW e-Golf 2019"
        assert meta["init"] == "ATSP0;"

    def test_default_init(self, tmp_path):
        root = tmp_path / "prof"
        _cmd_create(_args(path=root))
        meta = yaml.safe_load((root / "profile.yaml").read_text())
        assert meta["init"] == DEFAULT_INIT

    def test_created_ecus_dir_validates_empty(self, tmp_path):
        root = tmp_path / "prof"
        _cmd_create(_args(path=root))
        files = sorted((root / "ecus").glob("*.yaml"))
        errors, _w, stats = collect_pids_validation(files)
        assert errors == []
        assert stats["ecus"] == 0

    def test_rejects_nonempty_dir(self, tmp_path):
        root = tmp_path / "prof"
        root.mkdir()
        (root / "junk.txt").write_text("x")
        assert _cmd_create(_args(path=root)) == 1

    def test_force_allows_nonempty_dir(self, tmp_path):
        root = tmp_path / "prof"
        root.mkdir()
        (root / "junk.txt").write_text("x")
        assert _cmd_create(_args(path=root, force=True)) == 0
        assert (root / "profile.yaml").exists()

    def test_missing_car_model_noninteractive(self, tmp_path, monkeypatch):
        # No car_model + non-tty stdin → error, no scaffolding.
        monkeypatch.setattr("sys.stdin.isatty", lambda: False)
        root = tmp_path / "prof"
        assert _cmd_create(_args(path=root, car_model=None)) == 2
        assert not root.exists()

    def test_set_default_writes_config(self, tmp_path, monkeypatch):
        monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "cfg"))
        from canlib.config import load_config

        load_config.cache_clear()
        root = tmp_path / "prof"
        _cmd_create(_args(name="mycar", path=root, set_default=True))
        cfg = yaml.safe_load((tmp_path / "cfg" / "canair" / "config.yaml").read_text())
        assert cfg["default_profile"] == "mycar"


class TestSetConfigValue:
    def test_appends_new_key(self, tmp_path, monkeypatch):
        monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
        from canlib import config

        config.load_config.cache_clear()
        path = config.set_config_value("default_profile", "foo")
        assert "default_profile: foo" in path.read_text()
        config.load_config.cache_clear()
        assert config.load_config()["default_profile"] == "foo"

    def test_replaces_existing_uncommented_key(self, tmp_path, monkeypatch):
        monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
        from canlib import config

        config.load_config.cache_clear()
        config.set_config_value("default_profile", "foo")
        config.set_config_value("default_profile", "bar")
        text = (tmp_path / "canair" / "config.yaml").read_text()
        assert "default_profile: bar" in text
        assert "default_profile: foo" not in text

    def test_leaves_commented_line_untouched(self, tmp_path, monkeypatch):
        monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
        from canlib import config

        config.load_config.cache_clear()
        # Starter config has a commented `# default_profile: ...` line.
        path = config.set_config_value("default_profile", "foo")
        text = path.read_text()
        assert "# default_profile:" in text  # commented example preserved
        assert "default_profile: foo" in text


def _scaffold(tmp_path, name):
    """Create a minimal discoverable profile bundle under a profiles dir."""
    root = tmp_path / "profiles" / name
    (root / "ecus").mkdir(parents=True)
    (root / "profile.yaml").write_text(f'car_model: "{name}"\n')
    return root


class TestProfileUse:
    def test_sets_default_profile(self, tmp_path, monkeypatch):
        monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "cfg"))
        monkeypatch.setenv("CANAIR_PROFILES_DIR", str(tmp_path / "profiles"))
        from canlib import config

        config.load_config.cache_clear()
        _scaffold(tmp_path, "ev6")
        args = argparse.Namespace(name="ev6", profiles_dir=None)
        assert _cmd_use(args) == 0
        cfg = yaml.safe_load((tmp_path / "cfg" / "canair" / "config.yaml").read_text())
        assert cfg["default_profile"] == "ev6"

    def test_rejects_unknown_profile(self, tmp_path, monkeypatch, capsys):
        monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "cfg"))
        monkeypatch.setenv("CANAIR_PROFILES_DIR", str(tmp_path / "profiles"))
        from canlib import config

        config.load_config.cache_clear()
        _scaffold(tmp_path, "ev6")
        args = argparse.Namespace(name="nope", profiles_dir=None)
        assert _cmd_use(args) == 1
        err = capsys.readouterr().err
        assert "not found" in err
        # Nothing written.
        cfg_file = tmp_path / "cfg" / "canair" / "config.yaml"
        if cfg_file.exists():
            cfg = yaml.safe_load(cfg_file.read_text()) or {}
            assert cfg.get("default_profile") is None


class TestProfileListHint:
    def test_hint_when_no_default(self, tmp_path, monkeypatch, capsys):
        monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "cfg"))
        monkeypatch.setenv("CANAIR_PROFILES_DIR", str(tmp_path / "profiles"))
        from canlib import config, profile

        config.load_config.cache_clear()
        profile._active = None
        _scaffold(tmp_path, "ev6")
        _scaffold(tmp_path, "leaf")
        _cmd_list(argparse.Namespace(profiles_dir=None))
        out = capsys.readouterr().out
        assert "No default_profile set" in out
        assert "canair profile use" in out

    def test_no_hint_when_default_set(self, tmp_path, monkeypatch, capsys):
        monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "cfg"))
        monkeypatch.setenv("CANAIR_PROFILES_DIR", str(tmp_path / "profiles"))
        from canlib import config, profile

        config.load_config.cache_clear()
        profile._active = None
        _scaffold(tmp_path, "ev6")
        config.set_config_value("default_profile", "ev6")
        config.load_config.cache_clear()
        _cmd_list(argparse.Namespace(profiles_dir=None))
        out = capsys.readouterr().out
        assert "No default_profile set" not in out


class TestProfileListSnapshotWarning:
    def test_warns_when_running_uv_tool_snapshot(self, tmp_path, monkeypatch, capsys):
        monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "cfg"))
        monkeypatch.setenv("CANAIR_PROFILES_DIR", str(tmp_path / "profiles"))
        from canlib import config, install_context, profile

        config.load_config.cache_clear()
        profile._active = None
        _scaffold(tmp_path, "ev6")
        monkeypatch.setattr(install_context, "bundled_profiles_are_snapshot", lambda: True)
        _cmd_list(argparse.Namespace(profiles_dir=None))
        out = capsys.readouterr().out
        assert "frozen snapshot" in out
        assert "uv run canair" in out

    def test_no_warning_when_running_from_repo(self, tmp_path, monkeypatch, capsys):
        monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "cfg"))
        monkeypatch.setenv("CANAIR_PROFILES_DIR", str(tmp_path / "profiles"))
        from canlib import config, install_context, profile

        config.load_config.cache_clear()
        profile._active = None
        _scaffold(tmp_path, "ev6")
        monkeypatch.setattr(install_context, "bundled_profiles_are_snapshot", lambda: False)
        _cmd_list(argparse.Namespace(profiles_dir=None))
        out = capsys.readouterr().out
        assert "frozen snapshot" not in out


class TestProfileShow:
    def test_show_lists_all_bundle_components(self, tmp_path, monkeypatch, capsys):
        monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "cfg"))
        monkeypatch.setenv("CANAIR_PROFILES_DIR", str(tmp_path / "profiles"))
        from canlib import config, profile

        config.load_config.cache_clear()
        profile._active = None
        root = tmp_path / "profiles" / "ev6"
        _cmd_create(_args(name="ev6", path=root))
        (root / "signals").mkdir()
        (root / "signals" / "powertrain.yaml").write_text("signals: {}\n")
        (root / "references").mkdir()
        (root / "references" / "notes.txt").write_text("hi\n")

        assert _cmd_show(argparse.Namespace(name="ev6")) == 0
        out = capsys.readouterr().out
        # Every bundle component is surfaced (the gaps the change closes).
        for key in ("can_buses:", "states:", "signals:", "can logs:", "references:", "out:"):
            assert key in out
        assert "powertrain.yaml" in out
        assert "references" in out


class TestProfileDefaultDispatch:
    def test_bare_profile_lists_when_not_a_tty(self, tmp_path, monkeypatch, capsys):
        monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "cfg"))
        monkeypatch.setenv("CANAIR_PROFILES_DIR", str(tmp_path / "profiles"))
        from canlib import config, profile

        config.load_config.cache_clear()
        profile._active = None
        _scaffold(tmp_path, "ev6")
        monkeypatch.setattr("sys.stdin.isatty", lambda: False)
        monkeypatch.setattr("sys.stdout.isatty", lambda: True)

        assert _cmd_default(argparse.Namespace(profiles_dir=None)) == 0
        out = capsys.readouterr().out
        assert "ev6" in out  # plain list, no picker

    def test_bare_profile_picker_sets_default_on_a_tty(self, tmp_path, monkeypatch, capsys):
        monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "cfg"))
        monkeypatch.setenv("CANAIR_PROFILES_DIR", str(tmp_path / "profiles"))
        from canlib import config, profile

        config.load_config.cache_clear()
        profile._active = None
        _scaffold(tmp_path, "ev6")
        _scaffold(tmp_path, "leaf")
        monkeypatch.setattr("sys.stdin.isatty", lambda: True)
        monkeypatch.setattr("sys.stdout.isatty", lambda: True)
        # Stub the interactive selector to "choose" the second profile.
        chosen: dict = {}

        def _fake_select(items, **kw):
            chosen["items"] = list(items)
            return list(items)[1]

        monkeypatch.setattr("canlib.tui.select_from_list", _fake_select)

        assert _cmd_default(argparse.Namespace(profiles_dir=None)) == 0
        cfg = yaml.safe_load((tmp_path / "cfg" / "canair" / "config.yaml").read_text())
        assert cfg["default_profile"] == chosen["items"][1][0]

    def test_bare_profile_picker_cancel_makes_no_change(self, tmp_path, monkeypatch, capsys):
        monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "cfg"))
        monkeypatch.setenv("CANAIR_PROFILES_DIR", str(tmp_path / "profiles"))
        from canlib import config, profile

        config.load_config.cache_clear()
        profile._active = None
        _scaffold(tmp_path, "ev6")
        monkeypatch.setattr("sys.stdin.isatty", lambda: True)
        monkeypatch.setattr("sys.stdout.isatty", lambda: True)
        monkeypatch.setattr("canlib.tui.select_from_list", lambda items, **kw: None)

        assert _cmd_default(argparse.Namespace(profiles_dir=None)) == 0
        out = capsys.readouterr().out
        assert "Cancelled" in out
        cfg_file = tmp_path / "cfg" / "canair" / "config.yaml"
        if cfg_file.exists():
            cfg = yaml.safe_load(cfg_file.read_text()) or {}
            assert cfg.get("default_profile") is None


def _adopt_args(**kw) -> argparse.Namespace:
    base = {"name": "ev6", "profiles_dir": None, "set_default": False, "force": False}
    base.update(kw)
    return argparse.Namespace(**base)


class TestProfileAdopt:
    """`profile adopt` copies a read-only/bundled profile into the user directory."""

    @pytest.fixture(autouse=True)
    def _env(self, tmp_path, monkeypatch):
        monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "cfg"))
        monkeypatch.delenv("CANAIR_PROFILES_DIR", raising=False)
        # Pose tmp_path/profiles as the repo-bundled root — the lowest-precedence
        # search root, which is what adopting is meant to lift a profile out of.
        monkeypatch.setattr("canlib.profile.BUNDLED_PROFILES_DIR", tmp_path / "profiles")
        from canlib import config

        config.load_config.cache_clear()
        self.user_dir = tmp_path / "cfg" / "canair" / "profiles"

    def test_copies_bundle_members(self, tmp_path, capsys):
        source = _scaffold(tmp_path, "ev6")
        (source / "ecus" / "bms.yaml").write_text("tx_id: 0x7E4\n")
        (source / "captures").mkdir()
        (source / "captures" / "2026-08-05.json").write_text("{}")
        (source / "out").mkdir()
        (source / "out" / "autopid.json").write_text("[]")

        assert _cmd_adopt(_adopt_args()) == 0
        dest = self.user_dir / "ev6"
        assert (dest / "profile.yaml").exists()
        assert (dest / "ecus" / "bms.yaml").read_text() == "tx_id: 0x7E4\n"
        assert (dest / "captures" / "2026-08-05.json").exists()
        # Generated artifacts are regenerated, never copied.
        assert not (dest / "out").exists()
        out = capsys.readouterr().out
        assert "Adopted 'ev6'" in out
        assert str(dest) in out

    def test_adopted_copy_shadows_the_original(self, tmp_path):
        _scaffold(tmp_path, "ev6")
        assert _cmd_adopt(_adopt_args()) == 0
        from canlib.profile import resolve_profile

        assert resolve_profile("ev6").root == self.user_dir / "ev6"

    def test_skips_transient_journal_and_temp_files(self, tmp_path):
        source = _scaffold(tmp_path, "ev6")
        (source / "captures" / ".journal").mkdir(parents=True)
        (source / "captures" / ".journal" / "live.wal").write_text("x")
        (source / "captures" / "scratch.tmp").write_text("x")
        (source / "captures" / "2026-08-05.json").write_text("{}")

        assert _cmd_adopt(_adopt_args()) == 0
        dest = self.user_dir / "ev6"
        assert (dest / "captures" / "2026-08-05.json").exists()
        assert not (dest / "captures" / ".journal").exists()
        assert not (dest / "captures" / "scratch.tmp").exists()

    def test_unknown_profile_lists_what_exists(self, tmp_path, capsys):
        _scaffold(tmp_path, "ev6")
        assert _cmd_adopt(_adopt_args(name="nope")) == 1
        err = capsys.readouterr().err
        assert "not found" in err
        assert "ev6" in err

    def test_refuses_a_profile_that_is_already_a_user_copy(self, tmp_path, capsys):
        root = self.user_dir / "ev6"
        (root / "ecus").mkdir(parents=True)
        (root / "profile.yaml").write_text('car_model: "ev6"\n')

        assert _cmd_adopt(_adopt_args()) == 2
        assert "already" in capsys.readouterr().err

    def test_refuses_to_clobber_an_existing_copy_without_force(self, tmp_path, capsys):
        source = _scaffold(tmp_path, "ev6")
        (source / "ecus" / "bms.yaml").write_text("tx_id: 0x7E4\n")
        dest = self.user_dir / "ev6"
        (dest / "ecus").mkdir(parents=True)
        (dest / "ecus" / "mine.yaml").write_text("tx_id: 0x7A0\n")

        assert _cmd_adopt(_adopt_args()) == 1
        assert "--force" in capsys.readouterr().err
        assert not (dest / "ecus" / "bms.yaml").exists()

        assert _cmd_adopt(_adopt_args(force=True)) == 0
        assert (dest / "ecus" / "bms.yaml").exists()

    def test_refuses_when_a_higher_precedence_root_would_keep_winning(
        self, tmp_path, monkeypatch, capsys
    ):
        """A configured `profiles_dir` outranks the user dir, so a copy is inert."""
        elsewhere = tmp_path / "elsewhere"
        root = elsewhere / "ev6"
        (root / "ecus").mkdir(parents=True)
        (root / "profile.yaml").write_text('car_model: "ev6"\n')
        monkeypatch.setenv("CANAIR_PROFILES_DIR", str(elsewhere))

        assert _cmd_adopt(_adopt_args()) == 2
        err = capsys.readouterr().err
        assert "outranks" in err
        assert "CANAIR_PROFILES_DIR" in err
        assert not (self.user_dir / "ev6").exists()

    def test_set_default_records_the_choice(self, tmp_path):
        _scaffold(tmp_path, "ev6")
        assert _cmd_adopt(_adopt_args(set_default=True)) == 0
        cfg = yaml.safe_load((tmp_path / "cfg" / "canair" / "config.yaml").read_text())
        assert cfg["default_profile"] == "ev6"

    def test_warns_that_the_copy_stops_tracking_upstream(self, tmp_path, capsys):
        _scaffold(tmp_path, "ev6")
        assert _cmd_adopt(_adopt_args()) == 0
        out = capsys.readouterr().out
        assert "no longer tracks upstream" in out
        assert "profiles_dir" in out
