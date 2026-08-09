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

from canlib.cli import _GROUP_DEFAULTS, build_parser


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
