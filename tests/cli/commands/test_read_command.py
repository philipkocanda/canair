"""Tests for the top-level ``canair read`` command wiring (``query`` alias).

Guards the interactive-terminal hint that nudges toward ``canair monitor``: it
should appear on a TTY (non-JSON) and stay quiet when piped or emitting JSON so
machine output stays clean. Also confirms the kept ``query`` alias still parses
to the same command.
"""

from __future__ import annotations

import pytest

from canair.cli import build_parser


def _parse(argv: list[str]):
    return build_parser().parse_args(argv)


def _stub_live(monkeypatch):
    """Skip the ECU-name preflight (needs a profile) and the device connect."""
    from canair.cli.commands import read

    monkeypatch.setattr(read, "run_live", lambda args, operation: 0)
    monkeypatch.setattr(read, "query_ecu_error", lambda steps, pids, **__: None)
    monkeypatch.setattr(read, "load_pids", lambda *_, **__: {})


class TestMonitorHint:
    def test_hint_shown_on_tty(self, monkeypatch, capsys):
        from canair.cli.commands import read

        _stub_live(monkeypatch)
        monkeypatch.setattr("sys.stdout.isatty", lambda: True)

        args = _parse(["read", "BMS:2101"])
        assert read.run(args) == 0
        assert "canair monitor" in capsys.readouterr().err

    def test_hint_suppressed_when_piped(self, monkeypatch, capsys):
        from canair.cli.commands import read

        _stub_live(monkeypatch)
        monkeypatch.setattr("sys.stdout.isatty", lambda: False)

        args = _parse(["read", "BMS:2101"])
        assert read.run(args) == 0
        assert "canair monitor" not in capsys.readouterr().err

    def test_hint_suppressed_with_json(self, monkeypatch, capsys):
        from canair.cli.commands import read

        _stub_live(monkeypatch)
        monkeypatch.setattr("sys.stdout.isatty", lambda: True)

        args = _parse(["read", "BMS:2101", "--json"])
        assert read.run(args) == 0
        assert "canair monitor" not in capsys.readouterr().err


class TestQueryAlias:
    def test_query_alias_dispatches_to_read(self, monkeypatch, capsys):
        from canair.cli.commands import read

        _stub_live(monkeypatch)
        monkeypatch.setattr("sys.stdout.isatty", lambda: False)

        # The kept `query` alias parses to the same command/func as `read`.
        args = _parse(["query", "BMS:2101"])
        assert args.func is read.run
        assert read.run(args) == 0


class TestJsonFlag:
    """`--json` is accepted only where it is honoured (see `cli/live/parser.py`)."""

    def test_read_takes_json(self):
        assert _parse(["read", "BMS:2101", "--json"]).json is True

    def test_read_hands_json_to_the_pipeline(self, monkeypatch):
        import asyncio

        from canair.cli.live.handlers import multi

        captured = {}

        async def fake_mode_multi(*args, **kwargs):
            captured.update(kwargs)

        monkeypatch.setattr(multi, "mode_multi", fake_mode_multi)
        args = _parse(["read", "BMS:2101", "--json"])
        args.multi = ["query BMS:2101"]
        asyncio.run(multi.handle_multi(args, None, {}, "h"))  # type: ignore[arg-type]
        assert captured["as_json"] is True

    def test_read_json_without_steps_would_open_the_repl_so_it_is_refused(self, capsys):
        from canair.cli.commands import read

        assert read.run(_parse(["read", "--json"])) == 2
        assert "--json needs a STEP or --param" in capsys.readouterr().err

    def test_read_json_with_repl_is_refused(self, capsys):
        from canair.cli.commands import read

        assert read.run(_parse(["read", "BMS:2101", "--json", "--repl"])) == 2
        assert "--json cannot be combined with --repl" in capsys.readouterr().err

    def test_read_json_with_param_is_allowed(self, monkeypatch):
        from canair.cli.commands import read

        monkeypatch.setattr(read, "run_live", lambda args, operation: 0)
        assert read.run(_parse(["read", "--param", "SOC_BMS", "--json"])) == 0

    def test_monitor_does_not_accept_json(self, capsys):
        with pytest.raises(SystemExit) as exc:
            _parse(["monitor", "BMS:2101", "--json"])
        assert exc.value.code == 2
        assert "--json" in capsys.readouterr().err

    def test_repl_does_not_accept_json(self, capsys):
        with pytest.raises(SystemExit) as exc:
            _parse(["repl", "--json"])
        assert exc.value.code == 2
        assert "--json" in capsys.readouterr().err


def test_unlock_rejects_missing_session_before_connect(monkeypatch, tmp_path, capsys):
    from canair.cli.commands import read
    from canair.profile.model import Profile

    monkeypatch.setattr(read, "active", lambda: Profile("test", tmp_path))
    monkeypatch.setattr(read, "expand_step_groups", lambda steps: steps)
    monkeypatch.setattr(read, "load_pids", lambda **_: {"ecus": {"TEST": {"tx_id": 0x720}}})
    monkeypatch.setattr(read, "run_live", lambda *_: pytest.fail("must not connect"))
    args = _parse(
        ["read", "--security-key-file", str(tmp_path / "key"), "--yes", "unlock TEST --level 01"]
    )
    assert read.run(args) == 2
    assert "preceding session" in capsys.readouterr().err


def test_unlock_requires_confirmation_without_yes(monkeypatch, tmp_path, capsys):
    from canair.cli.commands import read
    from canair.profile.model import Profile

    key = tmp_path / "key"
    key.write_bytes(b"\xa1\xb2")
    key.chmod(0o600)
    monkeypatch.setattr(read, "active", lambda: Profile("test", tmp_path))
    monkeypatch.setattr(read, "expand_step_groups", lambda steps: steps)
    monkeypatch.setattr(read, "load_pids", lambda **_: {"ecus": {"TEST": {"tx_id": 0x720}}})
    monkeypatch.setattr(read, "run_live", lambda *_: pytest.fail("must not connect"))
    monkeypatch.setattr("sys.stdin.isatty", lambda: False)
    args = _parse(
        [
            "read",
            "--security-key-file",
            str(key),
            "session TEST --mode 03",
            "unlock TEST --level 01",
        ]
    )
    assert read.run(args) == 2
    assert "needs confirmation" in capsys.readouterr().err


def test_raw_only_pipeline_does_not_need_pid_definitions(monkeypatch, tmp_path):
    from canair.cli.commands import read
    from canair.profile.model import Profile

    monkeypatch.setattr(read, "active", lambda: Profile("test", tmp_path))
    monkeypatch.setattr(read, "expand_step_groups", lambda steps: steps)
    monkeypatch.setattr(read, "load_pids", lambda **_: pytest.fail("must not load definitions"))
    monkeypatch.setattr(read, "run_live", lambda *_: 0)
    assert read.run(_parse(["read", "raw 720:2101"])) == 0
