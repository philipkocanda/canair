"""Tests for the `canair --version` flag and single-sourced package version."""

from __future__ import annotations

import re

import pytest

import canlib
from canlib.cli import main


def test_package_exposes_version():
    assert isinstance(canlib.__version__, str)
    assert canlib.__version__


def test_version_matches_installed_metadata():
    from importlib.metadata import version

    # __version__ is single-sourced from the installed package metadata
    # (pyproject.toml [project].version), not duplicated in Python.
    assert canlib.__version__ == version("canair")


def test_version_flag_prints_version_and_exits(capsys):
    with pytest.raises(SystemExit) as exc:
        main(["--version"])
    # argparse's `version` action exits 0.
    assert exc.value.code == 0
    out = capsys.readouterr().out
    assert out.strip() == f"canair {canlib.__version__}"


def test_version_output_is_semver_shaped(capsys):
    with pytest.raises(SystemExit):
        main(["--version"])
    out = capsys.readouterr().out.strip()
    # "canair X.Y.Z" (allow dev/local suffixes after the core triple).
    assert re.match(r"^canair \d+\.\d+\.\d+", out)
