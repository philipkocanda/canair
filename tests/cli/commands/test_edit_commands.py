"""The shared edit pipeline: one error format and exit code, ``--no-validate`` for every engine."""

from __future__ import annotations

import pytest

from canair.cli import main

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


@pytest.fixture
def profile(tmp_path, monkeypatch):
    root = tmp_path / "car"
    (root / "ecus").mkdir(parents=True)
    (root / "profile.yaml").write_text('car_model: "Test car"\ninit: "ATSP6;"\n')
    (root / "ecus" / "testecu.yaml").write_text(ECU)
    (root / "groups.yaml").write_text("groups:\n  one:\n    members: [TESTECU]\n")
    (root / "vehicle_states.yaml").write_text("states:\n  - name: READY\n")
    monkeypatch.setenv("NO_COLOR", "1")
    monkeypatch.delenv("CANAIR_PROFILE", raising=False)
    return root


def _run(profile, *argv):
    return main(["--profile", str(profile), *argv])


REJECTED = [
    (["groups", "rm", "nosuch"], "group 'nosuch' not found"),
    (["groups", "add", "bad", "@other"], "cannot contain other groups"),
    (["states", "rm", "NOSUCH"], "state 'NOSUCH' not found"),
    (["states", "add", "X", "--when", "((("], ""),
    (["pids", "rm-param", "TESTECU", "2101", "NOSUCH"], "parameter 'NOSUCH' not found"),
    (["pids", "set-pid-period", "TESTECU", "2101", "0"], "period must be >= 1 ms"),
    (["pids", "set-addressing", "TESTECU", "--rx-id", "zz"], "--rx-id expects hex"),
    (["ecu", "revisions", "TESTECU", "rm", "nosuch"], "declares no revision"),
    (["action", "rm", "nosuch"], "no actions.yaml to edit"),
    (["signals", "rm", "bus", "0x1", "X"], "no signals file for bus 'bus'"),
]


@pytest.mark.parametrize(("argv", "words"), REJECTED, ids=[" ".join(a[:2]) for a, _ in REJECTED])
def test_every_engine_reports_a_rejected_edit_the_same_way(profile, capsys, argv, words):
    before = {p: p.read_text() for p in profile.rglob("*.yaml")}
    assert _run(profile, *argv) == 1
    err = capsys.readouterr().err
    assert err.startswith("error: "), err
    assert words in err
    assert {p: p.read_text() for p in profile.rglob("*.yaml")} == before


def test_a_successful_edit_confirms_with_the_full_path(profile, capsys):
    assert _run(profile, "groups", "add", "two", "TESTECU:2101") == 0
    out = capsys.readouterr().out
    assert "added group @two" in out
    assert str(profile / "groups.yaml") in out


@pytest.mark.parametrize(
    "argv",
    [
        ["pids", "set-pid-status", "TESTECU", "2101", "draft"],  # the text engine
        ["pids", "set-addressing", "TESTECU", "--rx-id", "0x701"],  # the ruamel engine
    ],
    ids=["text", "ruamel"],
)
def test_the_schema_check_can_veto_an_edit_and_no_validate_skips_it(
    profile, monkeypatch, capsys, argv
):
    path = profile / "ecus" / "testecu.yaml"
    before = path.read_text()
    calls = []

    def veto(p, *a, **k):
        calls.append(p)
        return False, "error: nope"

    monkeypatch.setattr("canair.validation.pids.validate_pids_file", veto)

    assert _run(profile, *argv) == 1
    assert "nope" in capsys.readouterr().err
    assert path.read_text() == before
    assert len(calls) == 1

    assert _run(profile, *argv, "--no-validate") == 0
    assert path.read_text() != before
    assert len(calls) == 1, "--no-validate must not call the schema check"


def test_clear_sentinel_and_verbs():
    from canair.cli.commands._edit import clear_sentinel, set_or_clear

    assert clear_sentinel("clear") is None
    assert clear_sentinel(" Clear ") is None
    assert clear_sentinel("2500") == "2500"
    assert set_or_clear("x") == "set"
    assert set_or_clear("") == "cleared"
    assert set_or_clear(None) == "cleared"


def test_subcommand_dispatch_falls_back_to_the_default():
    import argparse

    from canair.cli.commands._edit import subcommand_dispatch

    run = subcommand_dispatch("_func", lambda a: 7)
    assert run(argparse.Namespace()) == 7
    assert run(argparse.Namespace(_func=lambda a: 3)) == 3
