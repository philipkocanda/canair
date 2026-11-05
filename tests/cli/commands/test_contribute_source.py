"""Tests for what ``canair contribute`` leaves behind, in its workspace and in the source.

Two promises: a run never leaves its staged copy uncommitted in the managed
workspace (or the next run meets it as a leftover), and once the PR is open the
contributor can drop the changes it holds from their own canair checkout, and
only those, so pulling the merged PR does not conflict with them.
"""

from __future__ import annotations

import argparse
import io
import json
import subprocess
from pathlib import Path

from canair import contribute as C
from canair.captures import io as capture_io
from canair.cli.commands import _contribute_gates as gates
from canair.cli.commands import _contribute_report as report
from canair.cli.commands import contribute as cmd
from canair.contribute import source as S
from canair.profile import Profile

_ONE = {"sessions": [{"date": "2026-01-01", "captures": [{"time": "10:00", "pid": "2101"}]}]}
_TWO = {
    "sessions": [
        *_ONE["sessions"],
        {"date": "2026-01-01", "captures": [{"time": "11:00", "pid": "2102"}]},
    ]
}


def _ready() -> C.Preflight:
    return C.Preflight(gh="gh", git="git", authenticated=True)


def _git(repo, *args) -> str:
    return subprocess.run(
        ["git", "-C", str(repo), *args], check=True, capture_output=True, text=True
    ).stdout


def _checkout(path: Path) -> Path:
    """A canair-shaped checkout with ``profiles/testcar/`` committed on ``main``."""
    path.mkdir(parents=True)
    _git(path, "init", "-b", "main")
    _git(path, "config", "user.email", "t@example.com")
    _git(path, "config", "user.name", "T")
    prof = path / "profiles" / "testcar"
    (prof / "ecus").mkdir(parents=True)
    (prof / "ecus" / "bms.yaml").write_text("x\n")
    (prof / "profile.yaml").write_text("car_model: Test EV 2022\ninit: ATSP6;\n")
    (prof / "captures").mkdir()
    capture_io.dump_capture_file(prof / "captures" / "2026-01-01.json", _ONE)
    _git(path, "add", "-A")
    _git(path, "commit", "-m", "seed")
    return prof


def _clone(src: Path, dest: Path) -> Path:
    subprocess.run(["git", "clone", "-q", str(src), str(dest)], check=True)
    _git(dest, "config", "user.email", "t@example.com")
    _git(dest, "config", "user.name", "T")
    return dest


def _commit_in(ws: Path, rel: str, write) -> None:
    """Commit a change to ``rel`` in the workspace, standing in for the contribution."""
    write(ws / rel)
    _git(ws, "add", "-A")
    _git(ws, "commit", "-m", "contribution")


class TestFindChanges:
    def test_outside_a_git_checkout_is_skipped(self, tmp_path):
        prof = tmp_path / "profiles" / "testcar"
        prof.mkdir(parents=True)
        assert S.find_changes(_ready(), prof, "testcar", tmp_path / "ws") is None

    def test_a_profile_not_at_profiles_name_is_skipped(self, tmp_path):
        # A git repo of the user's own, or an old profile name: a pull of upstream
        # would never bring its changes back.
        prof = _checkout(tmp_path / "src")
        assert S.checkout_of(_ready(), prof, "othercar") is None

    def test_sorts_changes_by_whether_the_ref_holds_them(self, tmp_path):
        prof = _checkout(tmp_path / "src")
        ws = _clone(tmp_path / "src", tmp_path / "ws")
        (prof / "ecus" / "bms.yaml").write_text("x\ny\n")  # contributed
        (prof / "ecus" / "vcu.yaml").write_text("new\n")  # contributed, untracked
        (prof / "profile.yaml").write_text("car_model: local only\n")  # not contributed
        for rel, text in [("ecus/bms.yaml", "x\ny\n"), ("ecus/vcu.yaml", "new\n")]:
            (ws / "profiles" / "testcar" / rel).write_text(text)
        _git(ws, "add", "-A")
        _git(ws, "commit", "-m", "contribution")

        changes = S.find_changes(_ready(), prof, "testcar", ws)

        assert changes is not None
        assert sorted(changes.held) == [
            "profiles/testcar/ecus/bms.yaml",
            "profiles/testcar/ecus/vcu.yaml",
        ]
        assert changes.not_held == ["profiles/testcar/profile.yaml"]
        assert changes.untracked == {"profiles/testcar/ecus/vcu.yaml"}

    def test_a_capture_log_is_held_when_the_ref_has_all_its_sessions(self, tmp_path):
        # The contribution overlays captures onto upstream's, so the branch's copy
        # can hold more sessions than the source and still carry all of it.
        prof = _checkout(tmp_path / "src")
        ws = _clone(tmp_path / "src", tmp_path / "ws")
        log = "profiles/testcar/captures/2026-01-01.json"
        capture_io.dump_capture_file(prof / "captures" / "2026-01-01.json", {"sessions": []})
        _commit_in(ws, log, lambda p: capture_io.dump_capture_file(p, _TWO))

        changes = S.find_changes(_ready(), prof, "testcar", ws)

        assert changes is not None and changes.held == [log]

    def test_a_capture_session_the_ref_lacks_is_not_held(self, tmp_path):
        prof = _checkout(tmp_path / "src")
        ws = _clone(tmp_path / "src", tmp_path / "ws")
        capture_io.dump_capture_file(prof / "captures" / "2026-01-01.json", _TWO)

        changes = S.find_changes(_ready(), prof, "testcar", ws)

        assert changes is not None
        assert changes.held == []
        assert changes.not_held == ["profiles/testcar/captures/2026-01-01.json"]

    def test_a_deletion_is_held_only_when_the_ref_deletes_it_too(self, tmp_path):
        prof = _checkout(tmp_path / "src")
        ws = _clone(tmp_path / "src", tmp_path / "ws")
        (prof / "ecus" / "bms.yaml").unlink()

        changes = S.find_changes(_ready(), prof, "testcar", ws)
        assert changes is not None and changes.not_held == ["profiles/testcar/ecus/bms.yaml"]

        _git(ws, "rm", "-q", "profiles/testcar/ecus/bms.yaml")
        _git(ws, "commit", "-m", "contribution")
        changes = S.find_changes(_ready(), prof, "testcar", ws)
        assert changes is not None and changes.held == ["profiles/testcar/ecus/bms.yaml"]


class TestDiscard:
    def test_backs_up_then_restores_only_the_held_paths(self, tmp_path):
        prof = _checkout(tmp_path / "src")
        ws = _clone(tmp_path / "src", tmp_path / "ws")
        (prof / "ecus" / "bms.yaml").write_text("x\ny\n")
        (prof / "ecus" / "vcu.yaml").write_text("new\n")
        (prof / "profile.yaml").write_text("car_model: local only\n")
        (ws / "profiles" / "testcar" / "ecus" / "bms.yaml").write_text("x\ny\n")
        (ws / "profiles" / "testcar" / "ecus" / "vcu.yaml").write_text("new\n")
        _git(ws, "add", "-A")
        _git(ws, "commit", "-m", "contribution")
        changes = S.find_changes(_ready(), prof, "testcar", ws)
        assert changes is not None
        backup = tmp_path / "backup"

        assert S.discard(_ready(), changes, backup) == []

        assert (prof / "ecus" / "bms.yaml").read_text() == "x\n"
        assert not (prof / "ecus" / "vcu.yaml").exists()
        assert (prof / "profile.yaml").read_text() == "car_model: local only\n"
        assert (backup / "profiles/testcar/ecus/bms.yaml").read_text() == "x\ny\n"
        assert (backup / "profiles/testcar/ecus/vcu.yaml").read_text() == "new\n"
        assert "Contributed changes discarded" in (backup / "MANIFEST.txt").read_text()


class _Tty(io.StringIO):
    def isatty(self) -> bool:
        return True


def _args(**kw) -> argparse.Namespace:
    base = {
        "captures": True,
        "branch": "contribute/testcar-20261103",
        "title": None,
        "body": None,
        "repo_dir": None,
        "dry_run": False,
        "diff": False,
        "yes": True,
        "json": True,
        "local_changes": None,
    }
    base.update(kw)
    return argparse.Namespace(**base)


def _contributing(tmp_path, monkeypatch, *, pii: bool = False):
    """A source checkout with an uncommitted edit, and a managed workspace cloned from it."""
    prof = _checkout(tmp_path / "src")
    ws = _clone(tmp_path / "src", tmp_path / "contribute" / "canair")
    edit = "x\ny\n"
    (prof / "ecus" / "bms.yaml").write_text(edit)
    if pii:
        (prof / "profile.yaml").write_text("car_model: owner me@example.com\ninit: ATSP6;\n")

    ok = C.Step(cmd=["stub"], returncode=0, stdout="https://github.com/x/canair/pull/9\n")
    monkeypatch.setattr(cmd, "active", lambda: Profile("testcar", prof))
    monkeypatch.setattr(gates, "_validate", lambda p: (True, ""))
    monkeypatch.setattr(C, "preflight", _ready)
    monkeypatch.setattr(C, "workspace_dir", lambda: ws)
    monkeypatch.setattr(C, "push_branch", lambda *a, **k: ok)
    monkeypatch.setattr(C, "find_open_pr", lambda *a, **k: None)
    monkeypatch.setattr(C, "create_pr", lambda *a, **k: ok)
    return prof, ws


class TestAfterTheContribution:
    def test_discard_local_drops_the_contributed_changes(self, tmp_path, monkeypatch, capsys):
        prof, _ = _contributing(tmp_path, monkeypatch)

        rc = cmd.run(_args(local_changes="discard"))

        assert rc == report.OK
        local = json.loads(capsys.readouterr().out)["local_changes"]
        assert local["action"] == "discarded"
        assert local["paths"] == ["profiles/testcar/ecus/bms.yaml"]
        assert (prof / "ecus" / "bms.yaml").read_text() == "x\n"
        assert (Path(local["backup"]) / "profiles/testcar/ecus/bms.yaml").read_text() == "x\ny\n"
        assert Path(local["backup"]).parent == tmp_path / "contribute" / "backups"

    def test_yes_alone_keeps_them(self, tmp_path, monkeypatch, capsys):
        prof, _ = _contributing(tmp_path, monkeypatch)

        assert cmd.run(_args()) == report.OK

        local = json.loads(capsys.readouterr().out)["local_changes"]
        assert local["action"] == "kept" and local["backup"] is None
        assert (prof / "ecus" / "bms.yaml").read_text() == "x\ny\n"

    def test_asked_interactively(self, tmp_path, monkeypatch, capsys):
        prof, _ = _contributing(tmp_path, monkeypatch)
        asked: list[str] = []
        ask = lambda prompt, *a, **k: asked.append(prompt) or True  # noqa: E731
        monkeypatch.setattr(cmd, "confirm", ask)
        monkeypatch.setattr(report, "confirm", ask)
        monkeypatch.setattr("sys.stdin", _Tty())

        assert cmd.run(_args(yes=False, json=False)) == report.OK

        assert asked[-1] == "Discard them?"
        assert "profiles/testcar/ecus/bms.yaml" in capsys.readouterr().out
        assert (prof / "ecus" / "bms.yaml").read_text() == "x\n"

    def test_offered_when_upstream_already_has_them(self, tmp_path, monkeypatch, capsys):
        # The PR merged before the checkout pulled: nothing to contribute, but the
        # checkout's copies of the merged changes would still conflict on pull.
        prof, _ = _contributing(tmp_path, monkeypatch)
        _git(tmp_path / "src", "commit", "-qam", "merged upstream")  # upstream main has it
        _git(tmp_path / "src", "checkout", "-q", "-b", "local", "HEAD~1")
        (prof / "ecus" / "bms.yaml").write_text("x\ny\n")  # the checkout's copy, uncommitted

        assert cmd.run(_args(local_changes="discard")) == report.OK

        payload = json.loads(capsys.readouterr().out)
        assert payload["no_changes"] is True
        assert payload["local_changes"]["action"] == "discarded"
        assert (prof / "ecus" / "bms.yaml").read_text() == "x\n"

    def test_the_managed_workspace_is_clean_after_a_pr(self, tmp_path, monkeypatch, capsys):
        _, ws = _contributing(tmp_path, monkeypatch)

        assert cmd.run(_args()) == report.OK

        assert C.leftover_changes(_ready(), ws) == []

    def test_a_declined_gate_leaves_no_leftover(self, tmp_path, monkeypatch, capsys):
        # The PII gate refuses under --json without --yes after the copy was staged;
        # the next run must not meet that copy as "an earlier contribution".
        prof, ws = _contributing(tmp_path, monkeypatch, pii=True)

        assert cmd.run(_args(yes=False)) == report.CANNOT

        assert C.leftover_changes(_ready(), ws) == []
        assert "me@example.com" in (prof / "profile.yaml").read_text()
