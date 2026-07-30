"""Tests for canlib.grid_prompt — one-shot grid_region resolution/prompt."""

from __future__ import annotations

import canlib.grid_prompt as gp


def _reset():
    from canlib import config

    config.load_config.cache_clear()


class TestResolveGridRegion:
    def test_returns_configured_region(self, tmp_path, monkeypatch):
        monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
        _reset()
        from canlib.config import set_config_key

        set_config_key("grid_region", "US")
        _reset()
        assert gp.resolve_grid_region() == "US"

    def test_no_prompt_flag_returns_none_when_unset(self, tmp_path, monkeypatch):
        monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
        _reset()
        assert gp.resolve_grid_region(prompt=False) is None

    def test_non_interactive_notes_once_and_sets_sentinel(self, tmp_path, monkeypatch, capsys):
        monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
        _reset()
        monkeypatch.setattr(gp, "_is_interactive", lambda: False)

        assert gp.resolve_grid_region() is None
        err = capsys.readouterr().err
        assert "no grid_region set" in err

        # Second call: sentinel set, no repeat note.
        _reset()
        assert gp.resolve_grid_region() is None
        assert capsys.readouterr().err == ""

    def test_interactive_prompt_persists_choice(self, tmp_path, monkeypatch):
        monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
        _reset()
        monkeypatch.setattr(gp, "_is_interactive", lambda: True)
        monkeypatch.setattr("builtins.input", lambda _prompt: "us")

        assert gp.resolve_grid_region() == "US"
        _reset()
        from canlib.config import get_config_key

        assert get_config_key("grid_region") == "US"

    def test_interactive_skip_records_sentinel(self, tmp_path, monkeypatch):
        monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
        _reset()
        monkeypatch.setattr(gp, "_is_interactive", lambda: True)
        monkeypatch.setattr("builtins.input", lambda _prompt: "skip")

        assert gp.resolve_grid_region() is None
        _reset()
        from canlib.config import get_config_key

        assert get_config_key("grid_region") is None
        assert get_config_key("grid_region_prompted") is True
