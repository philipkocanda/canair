"""AGENTS.md may shrink, never grow back: its length and its source paths have ceilings.

The measure is ``scripts/check/agent_guide.py``; the ceilings are ``agents_md_lines`` and
``agents_md_code_paths`` in ``baseline.json``. A new design belongs in a docstring or a
``docs/development/`` page, not in the guide. Lowering a ceiling is
``uv run python -m tests.contracts.test_structure --write``. Raising one is a decision for the
user.
"""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
BASELINE = Path(__file__).with_name("baseline.json")


def _load_check(name: str):
    spec = importlib.util.spec_from_file_location(name, REPO / "scripts" / "check" / f"{name}.py")
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


agent_guide = _load_check("agent_guide")


def _ceilings() -> dict:
    return json.loads(BASELINE.read_text(encoding="utf8"))


def test_agent_guide_does_not_grow_back():
    found, ceilings = agent_guide.counts(), _ceilings()
    problems = [
        f"AGENTS.md has {found[key]} {label} (ceiling {ceilings[name]})"
        for key, label, name in (
            ("lines", "lines", "agents_md_lines"),
            ("code_paths", "src/canair paths", "agents_md_code_paths"),
        )
        if found[key] > ceilings[name]
    ]
    assert not problems, (
        "\n".join(problems) + "\nMove the design text next to the code it describes (a docstring, "
        "or docs/development/) and keep one line here that points at it."
    )


def test_counts_are_measured_on_the_text_given():
    text = "one\ntwo `src/canair/live/monitor/` and src/canair/core/paths.py::config_dir\nthree\n"
    assert agent_guide.counts(text) == {"lines": 3, "code_paths": 2}
