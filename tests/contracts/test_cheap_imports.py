"""Modules that other code imports for their vocabulary must stay cheap to import.

The CLI parser imports ``live/monitor/schedule.py`` and ``live/scan/presets.py`` at start-up, and
the view models of the Textual apps are rendered off a TTY by their commands. A sub-package
``__init__`` that imported its controller or app would load the whole monitor, or Textual, with
them (plans/2026-11-05-code-organization-5-live-and-tui-subpackages.md, rule 1).

Each module is imported in a fresh interpreter, because this process has long since imported
everything.
"""

from __future__ import annotations

import subprocess
import sys

import pytest

PROBE = """
import importlib, json, sys
module = sys.argv[1]
importlib.import_module(module)
print(json.dumps(sorted(sys.modules)))
"""

CASES = [
    ("canair.live.monitor.schedule", ("canair.live",), ("textual",)),
    ("canair.live.scan.presets", ("canair.live",), ("textual",)),
    ("canair.tui.apps.decode_plot.model", ("canair.tui.apps.decode_plot.app",), ("textual",)),
    ("canair.tui.apps.captures_step.model", ("canair.tui.apps.captures_step.app",), ("textual",)),
]


def _loaded(module: str) -> set[str]:
    out = subprocess.run(
        [sys.executable, "-c", PROBE, module], capture_output=True, text=True, check=True
    )
    import json

    return set(json.loads(out.stdout))


@pytest.mark.parametrize(("module", "forbidden_prefixes", "forbidden_roots"), CASES)
def test_the_module_loads_only_what_it_needs(module, forbidden_prefixes, forbidden_roots):
    loaded = _loaded(module)
    own = {module} | {module.rsplit(".", n)[0] for n in range(1, module.count(".") + 1)}
    extra = sorted(
        m
        for m in loaded
        if m not in own and any(m == p or m.startswith(p + ".") for p in forbidden_prefixes)
    )
    assert not extra, f"importing {module} also loaded {extra}"
    heavy = sorted(m for m in loaded if m.split(".")[0] in forbidden_roots)
    assert not heavy, f"importing {module} also loaded {heavy[:5]}"
