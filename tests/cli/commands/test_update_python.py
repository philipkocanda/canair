"""``canair update`` reinstalls on the chosen Python, and falls back when that fails."""

import subprocess
from types import SimpleNamespace

from canair.cli.commands import update as update_cmd

UV = "/usr/bin/uv"


def _done(returncode=0, stderr=""):
    return subprocess.CompletedProcess([], returncode, stdout="", stderr=stderr)


def _reinstall(monkeypatch, tmp_path, *, python, newest, results):
    calls = []

    def run(cmd):
        calls.append(cmd)
        return results[len(calls) - 1]

    monkeypatch.setattr(update_cmd, "newest_python", lambda uv, clone: newest)
    monkeypatch.setattr(update_cmd, "_run_uv", run)
    console = update_cmd.make_console()
    rc = update_cmd._do_reinstall(console, SimpleNamespace(python=python), UV, tmp_path)
    return rc, calls


def test_reinstalls_on_the_newest_managed_python(monkeypatch, tmp_path, capsys):
    rc, calls = _reinstall(monkeypatch, tmp_path, python=None, newest="3.14", results=[_done()])
    assert rc == update_cmd._OK
    assert calls == [
        [
            UV,
            "tool",
            "install",
            str(tmp_path),
            "--reinstall",
            "--managed-python",
            "--python",
            "3.14",
        ]
    ]
    assert "on Python 3.14" in capsys.readouterr().out


def test_falls_back_to_the_current_python_when_the_newest_fails(monkeypatch, tmp_path, capsys):
    rc, calls = _reinstall(
        monkeypatch,
        tmp_path,
        python=None,
        newest="3.15",
        results=[_done(1, "no wheels"), _done()],
    )
    assert rc == update_cmd._OK
    assert calls[1] == [UV, "tool", "install", str(tmp_path), "--reinstall"]
    out = capsys.readouterr().out
    assert "trying again on the tool's current Python" in out
    assert "no wheels" in out


def test_a_python_the_user_names_has_no_fallback(monkeypatch, tmp_path, capsys):
    rc, calls = _reinstall(
        monkeypatch, tmp_path, python="3.13", newest="3.15", results=[_done(1, "boom")]
    )
    assert rc == update_cmd._FAILED
    assert calls == [[UV, "tool", "install", str(tmp_path), "--reinstall", "--python", "3.13"]]
    assert "reinstall failed" in capsys.readouterr().out


def test_without_a_choice_it_keeps_the_current_python(monkeypatch, tmp_path):
    rc, calls = _reinstall(monkeypatch, tmp_path, python=None, newest=None, results=[_done()])
    assert rc == update_cmd._OK
    assert calls == [[UV, "tool", "install", str(tmp_path), "--reinstall"]]
