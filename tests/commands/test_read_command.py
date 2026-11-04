"""Tests for the top-level ``canair read`` command wiring (``query`` alias).

Guards the interactive-terminal hint that nudges toward ``canair monitor``: it
should appear on a TTY (non-JSON) and stay quiet when piped or emitting JSON so
machine output stays clean. Also confirms the kept ``query`` alias still parses
to the same command.
"""

from __future__ import annotations

from canlib.cli import build_parser


def _parse(argv: list[str]):
    return build_parser().parse_args(argv)


def _stub_live(monkeypatch):
    """Skip the ECU-name preflight (needs a profile) and the device connect."""
    from canlib.commands import read

    monkeypatch.setattr(read, "run_live", lambda args: 0)
    monkeypatch.setattr("canlib.modes.monitor.query_ecu_error", lambda steps, pids: None)
    monkeypatch.setattr("canlib.commands._live.load_pids", lambda: {})


class TestMonitorHint:
    def test_hint_shown_on_tty(self, monkeypatch, capsys):
        from canlib.commands import read

        _stub_live(monkeypatch)
        monkeypatch.setattr("sys.stdout.isatty", lambda: True)

        args = _parse(["read", "BMS:2101"])
        assert read.run(args) == 0
        assert "canair monitor" in capsys.readouterr().err

    def test_hint_suppressed_when_piped(self, monkeypatch, capsys):
        from canlib.commands import read

        _stub_live(monkeypatch)
        monkeypatch.setattr("sys.stdout.isatty", lambda: False)

        args = _parse(["read", "BMS:2101"])
        assert read.run(args) == 0
        assert "canair monitor" not in capsys.readouterr().err

    def test_hint_suppressed_with_json(self, monkeypatch, capsys):
        from canlib.commands import read

        _stub_live(monkeypatch)
        monkeypatch.setattr("sys.stdout.isatty", lambda: True)

        args = _parse(["read", "BMS:2101", "--json"])
        assert read.run(args) == 0
        assert "canair monitor" not in capsys.readouterr().err


class TestQueryAlias:
    def test_query_alias_dispatches_to_read(self, monkeypatch, capsys):
        from canlib.commands import read

        _stub_live(monkeypatch)
        monkeypatch.setattr("sys.stdout.isatty", lambda: False)

        # The kept `query` alias parses to the same command/func as `read`.
        args = _parse(["query", "BMS:2101"])
        assert args.func is read.run
        assert read.run(args) == 0
