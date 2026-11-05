"""CLI and TUI output rules: the deviations may shrink, never grow.

The rules are the ``cli-tui-standards`` skill; the decisions and the deviation report are
``plans/2026-11-05-cli-tui-standards.md``. The scanners are ``scripts/check/ui_standards.py``.
Each check keeps a per-file ceiling under ``ui_standards`` in ``baseline.json``: a new deviation
fails here, a fixed one has to leave the baseline (``uv run python -m
tests.contracts.test_structure --write``). Raising a ceiling is a decision for the user.
"""

from __future__ import annotations

import importlib.util
import json
import shlex
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
BASELINE = Path(__file__).with_name("baseline.json")


def _load_check(name: str):
    spec = importlib.util.spec_from_file_location(name, REPO / "scripts" / "check" / f"{name}.py")
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


ui_standards = _load_check("ui_standards")


def test_output_rules_have_no_new_deviation():
    baseline = json.loads(BASELINE.read_text(encoding="utf8")).get("ui_standards", {})
    problems = ui_standards.problems(baseline, ui_standards.counts())
    assert not problems, "\n".join(problems)


def test_every_check_has_a_named_home_or_none():
    """An allowed home must be a file that exists, or the rule has silently stopped applying."""
    missing = [
        f"{check}: {home}"
        for check, homes in ui_standards.ALLOWED.items()
        for home in sorted(homes)
        if not (ui_standards.SRC / home).exists()
        and not (check == "rich_console" and home == "render/console.py")
    ]
    assert not missing, "allowed home is gone: " + ", ".join(missing)


# Commands that read only the profile's definitions (not its captures, which only grow). With --json their stdout is one JSON
# document and nothing else: the notes, warnings and banners go to stderr.
JSON_COMMANDS = [
    "bus",
    "ecu",
    "ecu BMS",
    "ecu BMS pids",
    "states",
    "groups",
    "variants",
    "research",
    "action",
    "lock",
    "logs",
    "config show",
    "dtc log",
    "signals list",
    "io IGPM",
]


@pytest.mark.parametrize("command", JSON_COMMANDS)
def test_json_stdout_is_one_document(command, capsys):
    from canair.cli import main

    main(
        ["--profile", "hyundai-ioniq-ae-ev", "--variant", "28kwh", *shlex.split(command), "--json"]
    )
    out = capsys.readouterr().out
    try:
        json.loads(out)
    except json.JSONDecodeError as e:
        pytest.fail(
            f"`canair {command} --json` printed something that is not JSON: {e}\n{out[:200]}"
        )
