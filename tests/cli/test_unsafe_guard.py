"""While ``safety.allow_unsafe`` is on, every command starts with a warning banner, and
``--help`` lists ``--unsafe`` only on a terminal."""

from __future__ import annotations

import pytest

from canair.cli import build_parser, main
from canair.cli.live import parser as live_parser


@pytest.fixture
def user_config(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    path = tmp_path / "canair" / "config.yaml"
    path.parent.mkdir(parents=True)
    return path


class TestBanner:
    def test_shown_on_every_command_when_enabled(self, user_config, capsys):
        user_config.write_text("safety:\n  allow_unsafe: true\n")
        main(["config", "path"])
        captured = capsys.readouterr()
        assert "UNSAFE MODE IS ENABLED" in captured.err
        assert "canair config unset safety.allow_unsafe" in captured.err
        assert "UNSAFE" not in captured.out

    def test_shown_before_help(self, user_config, capsys):
        user_config.write_text("safety:\n  allow_unsafe: true\n")
        with pytest.raises(SystemExit):
            main(["--help"])
        assert "UNSAFE MODE IS ENABLED" in capsys.readouterr().err

    @pytest.mark.parametrize("text", [None, "safety:\n  allow_unsafe: false\n"])
    def test_absent_when_disabled(self, user_config, capsys, text):
        if text is not None:
            user_config.write_text(text)
        main(["config", "path"])
        assert "UNSAFE" not in capsys.readouterr().err


def _read_help() -> str:
    parser = build_parser()
    action = next(a for a in parser._actions if a.dest == "command")
    return action.choices["read"].format_help()


class TestHiddenFlag:
    def test_hidden_without_a_terminal(self, monkeypatch):
        monkeypatch.setattr(live_parser, "unsafe_flag_visible", lambda: False)
        assert "--unsafe" not in _read_help()

    def test_listed_on_a_terminal(self, monkeypatch):
        monkeypatch.setattr(live_parser, "unsafe_flag_visible", lambda: True)
        assert "--unsafe" in _read_help()

    def test_still_parses_when_hidden(self, monkeypatch):
        monkeypatch.setattr(live_parser, "unsafe_flag_visible", lambda: False)
        args = build_parser().parse_args(["read", "BMS:2101", "--unsafe"])
        assert args.unsafe is True
