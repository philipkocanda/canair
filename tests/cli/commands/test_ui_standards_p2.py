"""Message form, streams, prompts and exit codes of the authoring and housekeeping commands."""

from __future__ import annotations

import argparse
import io
from collections import Counter
from types import SimpleNamespace

import pytest

from canair.cli import main
from canair.core.prompt import PromptUnavailable
from canair.profile.model import profile_for_path

ECU = """\
TESTECU:
  tx_id: 0x700
  pids:
    2101:
      status: active
      parameters:
        SIG_A:
          expression: B4
"""

STATES = "states:\n  - name: READY\n"


@pytest.fixture
def profile(tmp_path, monkeypatch):
    root = tmp_path / "car"
    (root / "ecus").mkdir(parents=True)
    (root / "profile.yaml").write_text('car_model: "Test car"\ninit: "ATSP6;"\n')
    (root / "ecus" / "testecu.yaml").write_text(ECU)
    (root / "vehicle_states.yaml").write_text(STATES)
    monkeypatch.setenv("NO_COLOR", "1")
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "cfg"))
    monkeypatch.delenv("CANAIR_PROFILE", raising=False)
    return root


def _run(profile, *argv):
    return main(["--profile", str(profile), *argv])


class TestTopLevelErrors:
    def test_a_rejected_edit_is_error_lowercase_exit_1(self, profile, capsys):
        assert _run(profile, "states", "rm", "NOSUCH") == 1
        captured = capsys.readouterr()
        assert captured.err.startswith("error: ")
        assert captured.out == ""

    def test_a_prompt_with_no_terminal_is_error_exit_2(self, profile, monkeypatch, capsys):
        def refuse(args):
            raise PromptUnavailable("Do it?")

        monkeypatch.setattr("canair.cli.commands.states.parser.cmd_list", refuse)
        assert _run(profile, "states") == 2
        assert capsys.readouterr().err.startswith("error: Do it needs confirmation")

    def test_an_unknown_profile_is_error_exit_2(self, tmp_path, capsys):
        assert main(["--profile", str(tmp_path / "nope"), "states"]) == 2
        assert capsys.readouterr().err.startswith("error: ")


class TestStderrGating:
    """Messages on stderr are coloured by stderr's own terminal-ness, not stdout's."""

    def test_error_is_coloured_when_only_stderr_is_forced(self, profile, monkeypatch, capsys):
        monkeypatch.delenv("NO_COLOR")
        monkeypatch.setenv("FORCE_COLOR", "1")
        _run(profile, "states", "rm", "NOSUCH")
        assert capsys.readouterr().err.startswith("\x1b[91merror: ")

    def test_error_is_plain_when_piped(self, profile, monkeypatch, capsys):
        monkeypatch.delenv("FORCE_COLOR", raising=False)
        _run(profile, "states", "rm", "NOSUCH")
        assert "\x1b[" not in capsys.readouterr().err


class TestUnknownEcu:
    def test_unknown_ecu_goes_to_stderr_with_available_names(self, profile, capsys):
        assert _run(profile, "ecu", "NOPE") == 1
        captured = capsys.readouterr()
        assert captured.out == ""
        assert "error: unknown ECU 'NOPE'" in captured.err
        assert "hint: available ECUs: " in captured.err

    def test_invalid_groups_yaml_is_error_on_stderr(self, profile, capsys):
        (profile / "groups.yaml").write_text("groups: [not, a, mapping]\n")
        assert _run(profile, "groups") == 1
        captured = capsys.readouterr()
        assert captured.err.startswith("error: invalid groups.yaml:")
        assert captured.out == ""


class TestValidateFindings:
    def test_findings_are_on_stderr_and_the_summary_on_stdout(self, profile, capsys):
        (profile / "vehicle_states.yaml").write_text("states:\n  - name: READY\n    when: ((\n")
        assert _run(profile, "validate", "states") == 1
        captured = capsys.readouterr()
        assert "vehicle_states.yaml: " in captured.out and "error" in captured.out
        assert captured.err.startswith("error: vehicle_states.yaml: ")

    def test_pids_findings_use_the_message_form(self, profile, monkeypatch, capsys):
        import canair.cli.commands.validate.pids as mod
        from canair.cli.commands.validate.pids import run_pids
        from canair.validation.pids import PidsReport

        report = PidsReport(
            errors=["bms.yaml: bad"], warnings=["bms.yaml: odd"], stats={}, n_files=1
        )
        monkeypatch.setattr(mod, "check_pids", lambda files, profile: report)
        monkeypatch.setattr("canair.cli.profile_context._active", profile_for_path(profile))
        assert run_pids(None, False) == 1
        captured = capsys.readouterr()
        assert "warning: bms.yaml: odd" in captured.err
        assert "error: bms.yaml: bad" in captured.err
        assert "WARN" not in captured.err + captured.out
        assert "1 error(s), 1 warning(s)" in captured.out

    def test_a_clean_target_prints_ok_on_stdout_only(self, profile, capsys):
        assert _run(profile, "validate", "states") == 0
        captured = capsys.readouterr()
        assert "OK" in captured.out
        assert captured.err == ""


class TestConfirmations:
    def _plan(self, profile, monkeypatch):
        plan = SimpleNamespace(
            old="READY",
            new="GO",
            rewrites_history=True,
            states_file=profile / "vehicle_states.yaml",
            ecu_refs={},
            capture_refs={},
            log_refs={},
        )
        monkeypatch.setattr("canair.yaml_edit.state_rename.plan_state_rename", lambda *a, **k: plan)

    def test_states_rename_declined_exits_1(self, profile, monkeypatch):
        from canair.cli.commands.states import edit as states

        self._plan(profile, monkeypatch)
        monkeypatch.setattr(states, "confirm", lambda *a, **k: False)
        applied = []
        monkeypatch.setattr(
            "canair.yaml_edit.state_rename.apply_state_rename", lambda *a, **k: applied.append(1)
        )
        assert _run(profile, "states", "rename", "READY", "GO") == 1
        assert not applied

    def test_states_rename_without_terminal_or_yes_is_exit_2(self, profile, monkeypatch, capsys):
        self._plan(profile, monkeypatch)
        monkeypatch.setattr("sys.stdin", io.StringIO(""))
        assert _run(profile, "states", "rename", "READY", "GO") == 2
        assert "pass --yes to confirm" in capsys.readouterr().err

    def test_logs_clear_declined_exits_1_and_keeps_the_log(self, tmp_path, monkeypatch, capsys):
        from canair.cli.commands import logs

        monkeypatch.setattr("canair.core.eventlog.event_log_path", lambda: tmp_path / "x.log")
        cleared = []
        monkeypatch.setattr("canair.core.eventlog.clear_event_log", lambda: cleared.append(1) or 1)
        monkeypatch.setattr(logs, "confirm", lambda *a, **k: False)
        args = argparse.Namespace(path=False, clear=True, yes=False, json=False, lines=50)
        assert logs.run(args) == 1
        assert not cleared

    def test_logs_clear_without_terminal_is_prompt_unavailable(self, tmp_path, monkeypatch):
        from canair.cli.commands import logs

        monkeypatch.setattr("canair.core.eventlog.event_log_path", lambda: tmp_path / "x.log")
        monkeypatch.setattr("sys.stdin", io.StringIO(""))
        args = argparse.Namespace(path=False, clear=True, yes=False, json=False, lines=50)
        with pytest.raises(PromptUnavailable):
            logs.run(args)

    def test_wican_mode_set_declined_exits_1(self, monkeypatch):
        from canair.cli.commands.wican import mode

        monkeypatch.setattr(mode, "require_pro", lambda op: None)
        monkeypatch.setattr(mode, "require_v4_api", lambda *a, **k: None)
        monkeypatch.setattr(mode, "resolve_wican_url", lambda w: "http://x")
        monkeypatch.setattr("canair.device.wican.mode.current_protocol", lambda url: "elm327")
        monkeypatch.setattr(mode, "confirm", lambda *a, **k: False)
        sent = []
        monkeypatch.setattr(
            "canair.device.wican.mode.set_protocol", lambda *a, **__: sent.append(a)
        )
        args = argparse.Namespace(wican=None, protocol="slcan", yes=False, no_transport=True)
        assert mode.cmd_mode_set(args) == 1
        assert not sent


class TestWriteConfirmations:
    def test_config_set_confirms_with_the_full_path(self, profile, capsys):
        assert main(["config", "set", "default_wican", "home"]) == 0
        out = capsys.readouterr().out
        assert out.startswith("  ✓ default_wican = 'home'")
        assert "config.yaml" in out

    def test_config_unset_confirms_with_the_full_path(self, profile, capsys):
        main(["config", "set", "default_wican", "home"])
        capsys.readouterr()
        assert main(["config", "unset", "default_wican"]) == 0
        out = capsys.readouterr().out
        assert out.startswith("  ✓ removed default_wican") and "config.yaml" in out

    def test_config_set_error_is_message_form(self, profile, capsys):
        assert main(["config", "set", "transport.type", "bogus"]) == 2
        assert capsys.readouterr().err.startswith("error: invalid transport.type")


class TestBix:
    @pytest.mark.parametrize(
        "argv", [["zzzz"], ["-a", "6"], ["-a", "6G"], ["--raw"], ["-a", "62", "--ecu", "BMS"]]
    )
    def test_bad_input_is_error_exit_2(self, argv, capsys):
        try:
            code = main(["bix", *argv])
        except SystemExit as exc:
            code = exc.code
        assert code == 2
        assert capsys.readouterr().err.startswith("error: ")


class TestImportReportGating:
    def test_report_into_a_buffer_has_no_escapes_even_when_stdout_is_forced(self, monkeypatch):
        from pathlib import Path

        from canair.cli.commands.import_uds.log import _report
        from canair.interop.frame_log import Extraction

        monkeypatch.delenv("NO_COLOR", raising=False)
        monkeypatch.delenv("FORCE_COLOR", raising=False)
        extraction = Extraction(other_services=Counter({0x7F: 3}))
        buffer = io.StringIO()
        _report(extraction, Path("tap.asc"), json_out=False, file=buffer)
        text = buffer.getvalue()
        assert "ignored replies of other services: 0x7F ×3" in text
        assert "\x1b[" not in text


class TestWican:
    def test_autopid_download_failure_exits_1(self, monkeypatch, capsys):
        from canair.cli.commands.wican import autopid

        monkeypatch.setattr(autopid, "require_pro", lambda op: None)
        monkeypatch.setattr(autopid, "require_v4_api", lambda *a, **k: None)
        monkeypatch.setattr(autopid, "resolve_wican_url", lambda w: "http://x")
        monkeypatch.setattr(autopid, "download_profile", lambda url: None)
        assert autopid.cmd_autopid_download(argparse.Namespace(wican=None)) == 1
        captured = capsys.readouterr()
        assert captured.out == ""

    def test_stats_rule_follows_the_terminal_width(self, monkeypatch, capsys):
        from canair.cli.commands.wican import stats

        monkeypatch.setattr(stats, "terminal_columns", lambda: 60)
        stats.print_stats({"ecus": {}})
        assert "─" * 60 in capsys.readouterr().out
