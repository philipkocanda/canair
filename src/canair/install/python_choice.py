"""Which Python ``canair update`` reinstalls the tool on.

uv keeps a tool on the Python it was first installed with, and a fresh ``uv tool install``
takes the first Python it finds on the machine. Neither is the newest. So the update asks uv
for the CPython releases that uv itself can install, newest first, and takes the first one for
which every dependency installs from a prebuilt wheel.

A wheel lags a new Python by weeks or months. Without one, uv builds the dependency from
source: that needs a compiler, and it often drops the C extension. On Python 3.15, PyYAML built
without libyaml makes ``canair validate pids`` take 2.3 s instead of 0.5 s.

Only uv-managed Pythons qualify. A tool whose Python is removed stops working, and a system
Python goes away with ``brew upgrade`` or an OS update.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import tomllib
from pathlib import Path

_STABLE = re.compile(r"(\d+)\.(\d+)\.\d+")


def requires_python(clone: Path) -> str | None:
    """The clone's ``requires-python``, or None when it has none or cannot be read."""
    try:
        data = tomllib.loads((clone / "pyproject.toml").read_text(encoding="utf8"))
    except (OSError, tomllib.TOMLDecodeError):
        return None
    value = data.get("project", {}).get("requires-python")
    return value if isinstance(value, str) else None


def managed_pythons(uv: str, requires: str | None = None) -> list[str]:
    """The stable CPython minor versions (``"3.14"``) that uv can install here, newest first.

    ``uv python list`` names the installed ones and the downloads this uv release knows of,
    for this platform. Pre-releases and free-threaded or debug builds are left out.
    """
    cmd = [uv, "python", "list", "--managed-python", "--output-format", "json"]
    if requires:
        cmd.append(requires)
    done = subprocess.run(cmd, capture_output=True, text=True, check=False)
    if done.returncode != 0:
        return []
    try:
        rows = json.loads(done.stdout)
    except json.JSONDecodeError:
        return []
    minors: set[tuple[int, int]] = set()
    for row in rows if isinstance(rows, list) else []:
        if row.get("implementation") != "cpython" or row.get("variant") != "default":
            continue
        match = _STABLE.fullmatch(str(row.get("version", "")))
        if match:
            minors.add((int(match[1]), int(match[2])))
    return [f"{major}.{minor}" for major, minor in sorted(minors, reverse=True)]


def has_wheels(uv: str, clone: Path, python: str) -> bool:
    """Whether every dependency of the clone resolves to a prebuilt wheel on ``python``.

    A resolution only (``uv pip compile --no-build``): it downloads no Python and installs
    nothing.
    """
    cmd = [uv, "pip", "compile", "--quiet", "--no-build", "--python-version", python]
    cmd += [str(clone / "pyproject.toml"), "--output-file", os.devnull]
    done = subprocess.run(cmd, capture_output=True, text=True, check=False)
    return done.returncode == 0


def newest_python(uv: str, clone: Path) -> str | None:
    """The newest uv-managed CPython that the clone's dependencies all have wheels for.

    None when no candidate passes (offline, say): the reinstall then keeps the tool's Python.
    """
    for python in managed_pythons(uv, requires_python(clone)):
        if has_wheels(uv, clone, python):
            return python
    return None


def reinstall_command(uv: str, clone: Path, python: str | None, *, managed: bool) -> list[str]:
    """``uv tool install <clone> --reinstall``, on ``python`` when one is given.

    ``managed`` limits the request to uv-managed Pythons (the automatic choice). A version the
    user names is passed as given, so it may also be the path of an interpreter.
    """
    cmd = [uv, "tool", "install", str(clone), "--reinstall"]
    if python:
        if managed:
            cmd.append("--managed-python")
        cmd += ["--python", python]
    return cmd
