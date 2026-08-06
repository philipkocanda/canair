"""Tests for the `canair --version` flag and single-sourced package version."""

from __future__ import annotations

import re

import pytest

import canlib
from canlib.build_info import full_version
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
    # The reported version is the provenance-bearing one: the package version,
    # plus the checkout's branch/commit when running from a git working tree.
    assert out.strip() == f"canair {full_version()}"


def test_version_output_is_semver_shaped(capsys):
    with pytest.raises(SystemExit):
        main(["--version"])
    out = capsys.readouterr().out.strip()
    # "canair X.Y.Z" (allow dev/local suffixes after the core triple).
    assert re.match(r"^canair \d+\.\d+\.\d+", out)


def test_version_flag_reports_the_checkout_provenance(capsys, monkeypatch):
    """From a git checkout, --version names the branch and short commit."""
    from canlib import build_info

    monkeypatch.setattr(
        build_info,
        "running_build",
        lambda: build_info.GitBuild(branch="main", commit="343b244", dirty=False),
    )
    monkeypatch.setattr(canlib, "__version__", "1.2.3")
    with pytest.raises(SystemExit):
        main(["--version"])
    assert capsys.readouterr().out.strip() == "canair 1.2.3+main.343b244"


def test_version_is_not_resolved_while_building_the_parser(monkeypatch):
    """Building the parser must not shell out to git — it happens every run."""
    from canlib import build_info
    from canlib.cli import build_parser

    def boom():
        raise AssertionError("version resolved eagerly at parser-build time")

    monkeypatch.setattr(build_info, "full_version", boom)
    build_parser()
