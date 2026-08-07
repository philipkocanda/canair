"""Integration test: piped canair commands must emit zero ANSI escape codes.

The point of consolidating the palette onto :mod:`canlib.ansi` was to give the
tree one place to answer "should this be coloured?" — so that redirecting into
a file, piping into ``less``, or a caller (agent) parsing the output never sees
raw escape codes. Before this consolidation the leak was measured across the
whole CLI surface (see ``plans/2026-08-06-ansi-palette-consolidation.md``);
this test pins the fix so it can't regress.

Each subtest runs the CLI end-to-end (through a subprocess so the whole run —
including the subcommand-default injection in :func:`canlib.cli.main` — sees the
real captured stdout) against the bundled ``ioniq-2017`` profile, and asserts
the output contains no ``\\x1b[`` bytes. The command list intentionally spans
every command the plan counted as leaking, so a regression in any of them fails
here.
"""

from __future__ import annotations

import re
import subprocess
import sys
from collections.abc import Mapping

import pytest

ESCAPE_RE = re.compile(r"\x1b\[")


# Every command the plan called out as leaking (plus the four that were
# already gated, to catch a regression in the gating helpers). Each entry
# is ``(id, [args])`` — the id is only for the pytest node name.
COMMANDS: list[tuple[str, list[str]]] = [
    ("research", ["research"]),
    ("correlate-uds", ["correlate", "uds", "IGPM", "--last-session", "--top", "5"]),
    ("ecu-list", ["ecu"]),
    ("ecu-show", ["ecu", "BMS"]),
    ("hunt-uds", ["hunt", "uds", "AAF", "2181", "--against", "BMS:2101:SOC_BMS", "--top", "3"]),
    ("decode-value", ["decode", "BMS", "2101", "--last-session"]),
    ("decode-compact", ["decode", "BMS", "2101", "--compact", "--last-session"]),
    ("signals-list", ["signals", "list"]),
    ("investigate-uds", ["investigate", "uds", "IGPM", "22BC03"]),
    ("coverage-pid", ["coverage", "IGPM", "22BC02"]),
    ("coverage-ecu", ["coverage", "IGPM"]),
    ("captures-summary", ["captures", "uds", "--summary"]),
    ("captures-list", ["captures", "BMS", "2102", "--limit", "3"]),
    ("dtc-history", ["dtc", "--history"]),
    ("bus", ["bus"]),
    ("states", ["states"]),
    ("states-lookup", ["states", "READY"]),
    ("groups", ["groups"]),
    ("bix-annotate", ["bix", "-a", "62BC0300", "--ecu", "IGPM", "--pid", "22BC03"]),
    ("bix-table", ["bix", "--table"]),
]


def _run(args: list[str], env: Mapping[str, str] | None = None) -> str:
    """Run ``canair <args>`` as a subprocess and return its merged stdout+stderr.

    A subprocess (rather than in-process) is needed here because
    :func:`canlib.cli.main` rewrites ``argv`` for command-group defaults (bare
    ``ecu`` → ``ecu show``), and bypassing it would skip that. Slower per case
    but nothing else in these tests exercises that shape.
    """
    base_env = {
        "CANAIR_NO_UPDATE_CHECK": "1",
        "PATH": "/usr/bin:/bin",
    }
    if env:
        base_env.update(env)
    result = subprocess.run(
        [sys.executable, "-m", "canlib.cli", "--profile", "ioniq-2017", *args],
        capture_output=True,
        text=True,
        env=base_env,
        check=False,
    )
    return result.stdout + result.stderr


@pytest.mark.parametrize(("cid", "args"), COMMANDS, ids=[c[0] for c in COMMANDS])
def test_command_emits_no_escapes_when_piped(cid: str, args: list[str]) -> None:
    """Every listed command must produce ANSI-free output into a captured pipe."""
    out = _run(args)
    leaks = ESCAPE_RE.findall(out)
    assert not leaks, (
        f"{cid}: piped output contains {len(leaks)} ANSI escape(s) — "
        f"the command isn't gating on ansi.use_color(sys.stdout). "
        f"First 400 chars of output:\n{out[:400]!r}"
    )


def test_force_color_env_makes_a_piped_run_emit_escapes() -> None:
    """The mirror: with ``FORCE_COLOR=1`` set, a piped run *does* emit escapes.
    Proves the gate honours the env var (which the screenshot suite depends on),
    not just the stream's TTY-ness. Uses ``ecu`` because it always prints a
    coloured header.
    """
    out = _run(["ecu"], env={"FORCE_COLOR": "1"})
    assert ESCAPE_RE.search(out), (
        "FORCE_COLOR=1 should make even a piped run emit escapes — otherwise "
        "the screenshot suite (which sets FORCE_COLOR=1 to render into files) "
        f"loses colour. Got:\n{out[:400]!r}"
    )


def test_no_color_env_suppresses_escapes() -> None:
    """The other mirror: ``NO_COLOR=1`` suppresses every escape."""
    # Set FORCE_COLOR as well to prove NO_COLOR wins over it not, actually —
    # per the convention FORCE_COLOR wins. Just test NO_COLOR alone.
    out = _run(["ecu"], env={"NO_COLOR": "1"})
    assert not ESCAPE_RE.search(out), (
        f"NO_COLOR=1 should suppress every escape, got:\n{out[:400]!r}"
    )
