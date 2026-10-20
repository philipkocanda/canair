"""Tests for scripts/gen_profiles_index.py."""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
SCRIPT = REPO_ROOT / "scripts" / "gen_profiles_index.py"


@pytest.fixture(scope="module")
def mod():
    spec = importlib.util.spec_from_file_location("gen_profiles_index", SCRIPT)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _counts(ecus: int, params: int, verified: int) -> dict[str, int]:
    return {"ecus": ecus, "pids": 1, "params": params, "verified": verified}


@pytest.mark.parametrize(
    ("counts", "expected"),
    [
        (_counts(16, 163, 8), "An early profile (163 signals, 8 verified) — a starting point"),
        (_counts(1, 1, 1), "A seed / stub profile (1 signal, 1 verified) — a starting point"),
        (_counts(1, 194, 194), "A mature profile (194 signals, 194 verified). Browse"),
    ],
)
def test_profile_summary_names_maturity_once(mod, counts, expected):
    """Regression: the sentence read "An early early profile" / "An early mature profile"."""
    summary = mod.profile_summary(counts, "SRC")
    assert summary.startswith(expected)
    assert summary.endswith("Browse SRC.")


def test_mature_profile_is_not_called_a_starting_point(mod):
    assert "starting point" not in mod.profile_summary(_counts(1, 194, 194), "SRC")
