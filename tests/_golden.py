"""Shared harness for the golden-output gates.

Two modules pin committed stdout snapshots, for different reasons:

* :mod:`tests.test_analysis_golden` — the analysis verbs' **byte labels**
  (``Bnn``/``iN``/Torque/bix), because a silent off-by-PCI is invisible to
  free-running assertions and those labels are what ``--promote`` persists.
* :mod:`tests.test_captures_golden` — the ``captures`` **views' human text**
  (spacing, footers, per-view layout), which unit tests on the ``--json`` shapes
  cannot see.

Both need the same four things — a golden directory, the regen switch, an
ANSI/newline normalizer, and a "run one canair command against one profile"
helper — so they live here rather than being copy-pasted.

Regenerate after an *intended* change, then **read the diff**::

    CANAIR_REGEN_GOLDEN=1 uv run pytest tests/test_captures_golden.py -q
    git diff tests/fixtures/golden/
"""

from __future__ import annotations

import os
import re
from pathlib import Path

import pytest

from canlib import cli

GOLDEN_DIR = Path(__file__).parent / "fixtures" / "golden"
FIXTURE_PROFILES_DIR = Path(__file__).parent / "fixtures" / "profiles"
REGEN = os.environ.get("CANAIR_REGEN_GOLDEN") == "1"

# Flags that pin a case to a closed date range. ``captures/`` is append-only and
# grows with every ``--save``, so a golden over an unscoped query against a real
# profile pins a moving target — each golden module asserts its cases are stable
# by one of these, a frozen fixture profile, or a volume-independent verb.
SCOPE_FLAGS = frozenset({"--until", "--date", "--since"})

_ANSI_RE = re.compile(r"\x1b\[[0-9;]*[A-Za-z]")


def norm(text: str) -> str:
    """Strip ANSI and normalise line endings.

    ``decode --dump-bytes`` writes CSV through :mod:`csv`, which emits ``\\r\\n``,
    while ``Path.read_text`` universal-newlines it back to ``\\n`` — so an
    unnormalised comparison fails on a byte-identical run. Normalising also makes
    the goldens immune to git's autocrlf, and keeps them reviewable in a diff (a
    colour change is out of scope here — the screenshot check covers that).
    """
    return _ANSI_RE.sub("", text).replace("\r\n", "\n")


def run_cli(profile: str, argv: list[str], capsys) -> str:
    """Run one canair command against ``profile``, returning normalised output."""
    try:
        cli.main(["--profile", profile, *argv])
    except SystemExit:
        pass  # argparse/verb exit codes are not what we're pinning
    cap = capsys.readouterr()
    return norm(cap.out + cap.err)


def check_golden(name: str, got: str, *, hint: str) -> None:
    """Compare ``got`` against the committed golden ``name``, or regenerate it.

    ``hint`` is appended to the mismatch message to say *why* this particular
    golden matters, so the failure tells the reader what to look for in the diff.
    """
    path = GOLDEN_DIR / f"{name}.txt"

    if REGEN:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(got)
        pytest.skip(f"regenerated {path.name}")

    assert got.strip(), f"{name} produced no output — it would pin nothing"
    assert path.exists(), (
        f"missing golden {path.name} — regenerate with CANAIR_REGEN_GOLDEN=1 and review the diff"
    )
    want = norm(path.read_text())
    assert got == want, (
        f"{name}: output drifted from its golden.\n"
        f"If the change is intended, regenerate with CANAIR_REGEN_GOLDEN=1 and READ the diff — {hint}"
    )
