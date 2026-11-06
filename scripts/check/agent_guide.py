#!/usr/bin/env python3
"""Measure AGENTS.md: how long it is, and how many source paths it names.

``AGENTS.md`` holds the rules and the maps an agent needs in every session. Design text belongs
next to the code (a docstring, or a page in ``docs/development/``). Two numbers show when the
guide starts to grow back:

* ``lines``: the length of the file;
* ``code_paths``: the ``src/canair/...`` paths it names. Each one is a reference that a move
  can leave behind, and a sign of design text that describes one module.

``tests/contracts/test_agent_guide.py`` fails when either grows past its ceiling in
``tests/contracts/baseline.json`` (``agents_md_lines``, ``agents_md_code_paths``). The plan is
plans/2026-11-05-code-organization-10-agent-guide-cut.md.

Usage:
    uv run python scripts/check/agent_guide.py          # print both numbers
"""

from __future__ import annotations

import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
GUIDE = REPO_ROOT / "AGENTS.md"
_CODE_PATH = re.compile(r"src/canair(?:/[\w.\-]+)*")


def counts(text: str | None = None) -> dict[str, int]:
    """``{"lines": ..., "code_paths": ...}`` for AGENTS.md (or for ``text``)."""
    body = GUIDE.read_text(encoding="utf8") if text is None else text
    return {"lines": len(body.splitlines()), "code_paths": len(_CODE_PATH.findall(body))}


if __name__ == "__main__":
    for name, value in counts().items():
        print(f"{name}: {value}")
