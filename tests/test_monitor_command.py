"""Tests for the top-level ``canair monitor`` command.

The live monitor was promoted from the former ``canair query --monitor`` flag
into its own command. These tests guard that split at the parser level:

- ``monitor`` is registered and populates the shared-runtime attributes
  (``multi`` + ``monitor``) that ``dispatch_mode`` routes on;
- ``--interval`` sets the poll period;
- ``query`` no longer accepts ``--monitor`` (the flag moved).
"""

from __future__ import annotations

import pytest

from canlib.cli import build_parser


def _parse(argv: list[str]):
    return build_parser().parse_args(argv)


class TestMonitorRegistered:
    def test_monitor_is_a_command(self):
        args = _parse(["monitor", "BMS:2101"])
        assert args.command == "monitor"
        assert args.steps == ["BMS:2101"]

    def test_default_interval_is_five_seconds(self):
        args = _parse(["monitor", "BMS:2101"])
        assert args.interval == 5.0

    def test_interval_flag(self):
        args = _parse(["monitor", "BMS:2101", "--interval", "2"])
        assert args.interval == 2.0

    def test_keep_and_save_flags_live_on_monitor(self):
        args = _parse(["monitor", "BMS:2101", "--keep-all", "--save", "--label", "drive"])
        assert args.keep_all is True
        assert args.save is True
        assert args.label == "drive"


class TestMonitorRunWiring:
    """``run`` translates the surface flags into the attrs ``dispatch_mode`` reads."""

    def test_run_sets_multi_and_monitor(self, monkeypatch):
        from canlib.commands import monitor

        captured = {}

        def fake_run_live(args):
            captured["multi"] = args.multi
            captured["monitor"] = args.monitor
            return 0

        # Skip the ECU-name preflight (needs a profile) and the device connect.
        monkeypatch.setattr(monitor, "run_live", fake_run_live)
        monkeypatch.setattr("canlib.modes.monitor.query_ecu_error", lambda steps, pids: None)
        monkeypatch.setattr("canlib.commands._live.load_pids", lambda: {})

        args = _parse(["monitor", "BMS:2101", "--interval", "3"])
        assert monitor.run(args) == 0
        assert captured["multi"] == ["query BMS:2101"]
        assert captured["monitor"] == 3.0

    def test_run_requires_a_step(self):
        from canlib.commands import monitor

        args = _parse(["monitor"])
        assert monitor.run(args) == 2


class TestQueryNoLongerMonitors:
    def test_query_rejects_monitor_flag(self):
        with pytest.raises(SystemExit):
            _parse(["query", "BMS:2101", "--monitor"])


class TestMonitorGroupExpansion:
    """``@group`` refs expand into their member selectors before dispatch."""

    def _stub(self, monkeypatch, captured):
        from canlib.commands import monitor
        from canlib.ecu_groups import Group

        def fake_run_live(args):
            captured["multi"] = args.multi
            return 0

        monkeypatch.setattr(monitor, "run_live", fake_run_live)
        monkeypatch.setattr("canlib.modes.monitor.query_ecu_error", lambda steps, pids: None)
        monkeypatch.setattr("canlib.commands._live.load_pids", lambda: {})
        monkeypatch.setattr(
            "canlib.ecu_groups.load_groups",
            lambda profile=None: {
                "charging": Group("charging", "", ("BMS:2101", "OBC", "VCU")),
            },
        )
        return monitor

    def test_group_expands(self, monkeypatch):
        captured: dict = {}
        monitor = self._stub(monkeypatch, captured)
        assert monitor.run(_parse(["monitor", "@charging"])) == 0
        assert captured["multi"] == ["query BMS:2101 OBC VCU"]

    def test_group_plus_extra_selector(self, monkeypatch):
        captured: dict = {}
        monitor = self._stub(monkeypatch, captured)
        assert monitor.run(_parse(["monitor", "@charging", "CLU:220B"])) == 0
        assert captured["multi"] == ["query BMS:2101 OBC VCU", "query CLU:220B"]

    def test_unknown_group_errors(self, monkeypatch):
        captured: dict = {}
        monitor = self._stub(monkeypatch, captured)
        assert monitor.run(_parse(["monitor", "@bogus"])) == 2
