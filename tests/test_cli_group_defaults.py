"""Pins `canlib.cli._GROUP_DEFAULTS` against the commands' real subparsers.

Default-kind injection runs on raw argv, before a parser exists, so each group's
known-kind set is duplicated in `cli.py`. A kind missing from that copy is not an
error: the token is treated as *not* a kind, the default kind is injected ahead of
it, and argparse then rejects it as a stray positional — so a brand-new
subcommand fails with a confusing "unrecognized arguments" instead of running.
This test makes that drift fail loudly at the source instead.
"""

from __future__ import annotations

import argparse

import pytest

from canlib.cli import _GROUP_DEFAULTS, _RELOCATED_COMMANDS, build_parser


def _subparser_kinds(parser: argparse.ArgumentParser, command: str) -> set[str]:
    """Every kind (including aliases) registered under ``command``'s subparsers."""
    top = next(a for a in parser._actions if isinstance(a, argparse._SubParsersAction))
    cmd = top.choices[command]
    sub = next((a for a in cmd._actions if isinstance(a, argparse._SubParsersAction)), None)
    assert sub is not None, f"{command!r} declares group defaults but has no subparsers"
    return set(sub.choices)


@pytest.mark.parametrize("command", sorted(_GROUP_DEFAULTS))
def test_declared_kinds_match_the_real_subparsers(command):
    kinds, default = _GROUP_DEFAULTS[command]
    real = _subparser_kinds(build_parser(), command)
    missing = real - kinds
    assert not missing, (
        f"{command!r} has subcommand(s) {sorted(missing)} missing from "
        f"cli._GROUP_DEFAULTS — they would be swallowed as the default kind"
    )
    stale = kinds - real
    assert not stale, f"{command!r} declares kind(s) {sorted(stale)} that no longer exist"
    assert default in real, f"{command!r} default kind {default!r} is not a real subcommand"


def _resolve_command_path(parser: argparse.ArgumentParser, path: str) -> bool:
    """True if ``path`` (e.g. "wican mode") walks a real chain of subparsers."""
    node = parser
    for token in path.split():
        sub = next((a for a in node._actions if isinstance(a, argparse._SubParsersAction)), None)
        if sub is None or token not in sub.choices:
            return False
        node = sub.choices[token]
    return True


@pytest.mark.parametrize("token, dest", sorted(_RELOCATED_COMMANDS.items()))
def test_relocation_targets_are_real_commands(token, dest):
    # A hint that points at a command that does not exist is worse than none.
    assert _resolve_command_path(build_parser(), dest), (
        f"cli._RELOCATED_COMMANDS[{token!r}] -> {dest!r} is not a real command path"
    )


def test_a_relocated_command_prints_a_pointer(capsys):
    with pytest.raises(SystemExit) as exc:
        build_parser().parse_args(["mode", "set", "slcan"])
    assert exc.value.code == 2
    err = capsys.readouterr().err
    assert "hint: 'mode' is not a canair command" in err
    assert "canair wican mode" in err


def test_an_unknown_command_gets_no_pointer(capsys):
    with pytest.raises(SystemExit) as exc:
        build_parser().parse_args(["frobnicate"])
    assert exc.value.code == 2
    err = capsys.readouterr().err
    assert "invalid choice: 'frobnicate'" in err
    assert "hint:" not in err
