"""A command that picks one device goes through ``select_device``, so it gets the fallback.

``resolve_transport`` and ``resolve_wican_url`` return the selected device without probing it:
a command that calls them directly fails on a down default device while the live commands fall
back to another one. ``docs/development/link-health.md`` ("Choosing a device") has the design.
"""

from __future__ import annotations

import ast
from pathlib import Path

CLI = Path(__file__).resolve().parents[2] / "src" / "canair" / "cli"
UNPROBED = {"resolve_transport", "resolve_wican_url"}

# Each call here is deliberate: ``config`` only displays the selected device, and the ``wican``
# helper applies the strict explicit ``--wican`` rule before it probes anything.
ALLOWED = {
    "commands/config/show.py": {"resolve_transport"},
    "commands/wican/common.py": {"resolve_wican_url"},
}


def _called(path: Path) -> set[str]:
    names: set[str] = set()
    for node in ast.walk(ast.parse(path.read_text(encoding="utf8"))):
        if isinstance(node, ast.Call):
            func = node.func
            name = func.id if isinstance(func, ast.Name) else getattr(func, "attr", "")
            if name in UNPROBED:
                names.add(name)
    return names


def test_cli_does_not_pick_a_device_without_probing_it():
    problems = []
    for path in sorted(CLI.rglob("*.py")):
        rel = path.relative_to(CLI).as_posix()
        extra = _called(path) - ALLOWED.get(rel, set())
        if extra:
            problems.append(f"{rel} calls {', '.join(sorted(extra))}")
    assert not problems, (
        "These commands skip the connect-time fallback; use "
        "canair.transport.select_device (or wican/common.py::resolve_device_url):\n"
        + "\n".join(problems)
    )
