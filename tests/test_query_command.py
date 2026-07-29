"""Tests for the top-level ``canair query`` command wiring.

Guards the interactive-terminal hint that nudges toward ``canair monitor``: it
should appear on a TTY (non-JSON) and stay quiet when piped or emitting JSON so
machine output stays clean.
"""

from __future__ import annotations

from canlib.cli import build_parser


def _parse(argv: list[str]):
    return build_parser().parse_args(argv)


def _stub_live(monkeypatch):
    """Skip the ECU-name preflight (needs a profile) and the device connect."""
    from canlib.commands import query

    monkeypatch.setattr(query, "run_live", lambda args: 0)
    monkeypatch.setattr("canlib.modes.monitor.query_ecu_error", lambda steps, pids: None)
    monkeypatch.setattr("canlib.commands._live.load_pids", lambda: {})


class TestMonitorHint:
    def test_hint_shown_on_tty(self, monkeypatch, capsys):
        from canlib.commands import query

        _stub_live(monkeypatch)
        monkeypatch.setattr("sys.stdout.isatty", lambda: True)

        args = _parse(["query", "BMS:2101"])
        assert query.run(args) == 0
        assert "canair monitor" in capsys.readouterr().err

    def test_hint_suppressed_when_piped(self, monkeypatch, capsys):
        from canlib.commands import query

        _stub_live(monkeypatch)
        monkeypatch.setattr("sys.stdout.isatty", lambda: False)

        args = _parse(["query", "BMS:2101"])
        assert query.run(args) == 0
        assert "canair monitor" not in capsys.readouterr().err

    def test_hint_suppressed_with_json(self, monkeypatch, capsys):
        from canlib.commands import query

        _stub_live(monkeypatch)
        monkeypatch.setattr("sys.stdout.isatty", lambda: True)

        args = _parse(["query", "BMS:2101", "--json"])
        assert query.run(args) == 0
        assert "canair monitor" not in capsys.readouterr().err
