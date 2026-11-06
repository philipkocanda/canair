import json
import subprocess

from canair.install import python_choice as pc

UV = "/usr/bin/uv"


def _done(returncode=0, stdout=""):
    return subprocess.CompletedProcess([], returncode, stdout=stdout, stderr="")


def _row(version, implementation="cpython", variant="default"):
    return {"version": version, "implementation": implementation, "variant": variant}


def test_managed_pythons_lists_stable_cpython_minors_newest_first(monkeypatch):
    rows = [
        _row("3.16.0a1"),
        _row("3.15.2"),
        _row("3.15.2", variant="freethreaded"),
        _row("3.13.5"),
        _row("3.13.18"),
        _row("3.14.10"),
        _row("3.11.9", implementation="pypy"),
    ]
    calls = []

    def run(cmd, **_):
        calls.append(cmd)
        return _done(stdout=json.dumps(rows))

    monkeypatch.setattr(pc.subprocess, "run", run)
    assert pc.managed_pythons(UV, ">=3.12") == ["3.15", "3.14", "3.13"]
    assert calls == [
        [UV, "python", "list", "--managed-python", "--output-format", "json", ">=3.12"]
    ]


def test_managed_pythons_is_empty_when_uv_fails_or_says_nonsense(monkeypatch):
    monkeypatch.setattr(pc.subprocess, "run", lambda *a, **k: _done(returncode=2))
    assert pc.managed_pythons(UV) == []
    monkeypatch.setattr(pc.subprocess, "run", lambda *a, **k: _done(stdout="not json"))
    assert pc.managed_pythons(UV) == []


def test_has_wheels_resolves_without_building(monkeypatch, tmp_path):
    calls = []

    def run(cmd, **_):
        calls.append(cmd)
        return _done(returncode=1)

    monkeypatch.setattr(pc.subprocess, "run", run)
    assert not pc.has_wheels(UV, tmp_path, "3.15")
    cmd = calls[0]
    assert cmd[:3] == [UV, "pip", "compile"]
    assert "--no-build" in cmd
    assert cmd[cmd.index("--python-version") + 1] == "3.15"
    assert str(tmp_path / "pyproject.toml") in cmd


def test_newest_python_skips_a_python_whose_dependencies_lack_wheels(monkeypatch, tmp_path):
    (tmp_path / "pyproject.toml").write_text('[project]\nrequires-python = ">=3.12"\n')
    seen = []
    monkeypatch.setattr(pc, "managed_pythons", lambda uv, req: seen.append(req) or ["3.15", "3.14"])
    monkeypatch.setattr(pc, "has_wheels", lambda uv, clone, python: python == "3.14")
    assert pc.newest_python(UV, tmp_path) == "3.14"
    assert seen == [">=3.12"]


def test_newest_python_is_none_when_nothing_qualifies(monkeypatch, tmp_path):
    monkeypatch.setattr(pc, "managed_pythons", lambda uv, req: ["3.15"])
    monkeypatch.setattr(pc, "has_wheels", lambda uv, clone, python: False)
    assert pc.newest_python(UV, tmp_path) is None


def test_requires_python_reads_the_clone(tmp_path):
    assert pc.requires_python(tmp_path) is None
    (tmp_path / "pyproject.toml").write_text('[project]\nrequires-python = ">=3.12"\n')
    assert pc.requires_python(tmp_path) == ">=3.12"
    (tmp_path / "pyproject.toml").write_text("not = [toml")
    assert pc.requires_python(tmp_path) is None


def test_reinstall_command(tmp_path):
    base = [UV, "tool", "install", str(tmp_path), "--reinstall"]
    assert pc.reinstall_command(UV, tmp_path, None, managed=True) == base
    assert pc.reinstall_command(UV, tmp_path, "3.14", managed=True) == [
        *base,
        "--managed-python",
        "--python",
        "3.14",
    ]
    assert pc.reinstall_command(UV, tmp_path, "/opt/py", managed=False) == [
        *base,
        "--python",
        "/opt/py",
    ]
