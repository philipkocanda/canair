"""Tests for the capture-file merge driver (canlib.captures_merge + the
``canair captures merge-driver`` command).

Covers the pure 3-way union (disjoint appends, shared history, deletions, the
divergent-edit conflict guard, determinism/order-independence), the driver
command's file I/O + exit codes, and an end-to-end real ``git merge`` in a
temporary repo with the driver registered — reproducing the same-day
both-append conflict that motivated the feature.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from canlib import capture_io, captures_merge
from canlib.commands.captures import merge_driver


def _session(label, time, *, date="2026-07-28", ecu="0x7EC", pid="2101", payload="6101AA"):
    return {
        "date": date,
        "label": label,
        "vehicle_states": ["ready", "parked"],
        "captures": [{"ecu": ecu, "pid": pid, "payload": payload, "time": time}],
    }


def _doc(*sessions):
    return {"sessions": list(sessions)}


# --------------------------------------------------------------------------
# Pure union function
# --------------------------------------------------------------------------


class TestMergeSessions:
    def test_disjoint_appends_are_unioned(self):
        shared = _session("shared", "10:00:00")
        base = _doc(shared)
        ours = _doc(shared, _session("ours-only", "12:00:00"))
        theirs = _doc(shared, _session("theirs-only", "11:00:00"))

        merged = captures_merge.merge_sessions(base, ours, theirs)

        labels = [s["label"] for s in merged]
        assert labels == ["shared", "theirs-only", "ours-only"]  # chronological

    def test_shared_history_not_duplicated(self):
        a = _session("a", "10:00:00")
        b = _session("b", "11:00:00")
        base = _doc(a)
        ours = _doc(a, b)
        theirs = _doc(a, b)  # both added the same session
        merged = captures_merge.merge_sessions(base, ours, theirs)
        assert [s["label"] for s in merged] == ["a", "b"]

    def test_deletion_on_one_side_unchanged_on_other_is_honoured(self):
        a = _session("a", "10:00:00")
        b = _session("b", "11:00:00")
        base = _doc(a, b)
        ours = _doc(a)  # we deleted b
        theirs = _doc(a, b)  # they left it untouched
        merged = captures_merge.merge_sessions(base, ours, theirs)
        assert [s["label"] for s in merged] == ["a"]

    def test_empty_base_two_new_files(self):
        base = _doc()
        ours = _doc(_session("x", "09:00:00"))
        theirs = _doc(_session("y", "08:00:00"))
        merged = captures_merge.merge_sessions(base, ours, theirs)
        assert [s["label"] for s in merged] == ["y", "x"]

    def test_divergent_edit_of_same_session_raises(self):
        a = _session("a", "10:00:00", payload="6101AA")
        base = _doc(a)
        ours = _doc(_session("a", "10:00:00", payload="6101BB"))  # edited
        theirs = _doc()  # deleted
        with pytest.raises(captures_merge.MergeConflict):
            captures_merge.merge_sessions(base, ours, theirs)

    def test_order_independent(self):
        base = _doc(_session("shared", "10:00:00"))
        o = _session("o", "12:00:00")
        t = _session("t", "11:00:00")
        ours = _doc(_session("shared", "10:00:00"), o)
        theirs = _doc(_session("shared", "10:00:00"), t)
        ab = captures_merge.merge_sessions(base, ours, theirs)
        ba = captures_merge.merge_sessions(base, theirs, ours)
        assert ab == ba

    def test_tolerates_missing_sessions_key(self):
        assert captures_merge.merge_sessions({}, {}, {}) == []


# --------------------------------------------------------------------------
# Contribute overlay (order-preserving, never deletes, no-op detectable)
# --------------------------------------------------------------------------


class TestOverlayDocuments:
    def test_nothing_new_returns_none(self):
        # The `canair contribute` no-op case: the caller must be able to tell
        # "adds nothing" so it can leave the upstream file's bytes untouched.
        doc = _doc(_session("a", "10:00:00"), _session("b", "11:00:00"))
        assert captures_merge.overlay_documents(doc, doc) is None

    def test_source_subset_of_upstream_returns_none(self):
        a, b = _session("a", "10:00:00"), _session("b", "11:00:00")
        assert captures_merge.overlay_documents(_doc(a, b), _doc(a)) is None

    def test_preserves_upstream_order_and_appends_new(self):
        # Upstream order is NOT the canonical sort order (labels descend, and
        # untimed sessions would sort by label) — it must survive verbatim.
        first = _session("zzz", "")
        second = _session("aaa", "")
        new = _session("mmm", "")
        merged = captures_merge.overlay_documents(_doc(first, second), _doc(second, new))
        assert merged is not None
        assert [s["label"] for s in merged["sessions"]] == ["zzz", "aaa", "mmm"]

    def test_never_drops_an_upstream_session(self):
        # A source that is merely *behind* upstream must not propose a deletion.
        upstream = _doc(_session("upstream-only", "10:00:00"))
        source = _doc(_session("source-only", "11:00:00"))
        merged = captures_merge.overlay_documents(upstream, source)
        assert merged is not None
        assert [s["label"] for s in merged["sessions"]] == ["upstream-only", "source-only"]

    def test_preserves_other_top_level_keys(self):
        merged = captures_merge.overlay_documents(
            {"schema": 2, "sessions": []}, _doc(_session("new", "10:00:00"))
        )
        assert merged is not None
        assert merged["schema"] == 2

    def test_tolerates_missing_sessions_key(self):
        assert captures_merge.overlay_documents({}, {}) is None
        merged = captures_merge.overlay_documents({}, _doc(_session("new", "10:00:00")))
        assert merged is not None
        assert [s["label"] for s in merged["sessions"]] == ["new"]


# --------------------------------------------------------------------------
# Driver command: file I/O + exit codes
# --------------------------------------------------------------------------


class TestDriverCommand:
    def _write(self, path: Path, doc) -> None:
        capture_io.dump_capture_file(path, doc)

    def test_run_driver_writes_union_and_returns_zero(self, tmp_path):
        base = tmp_path / "base.json"
        ours = tmp_path / "ours.json"
        theirs = tmp_path / "theirs.json"
        shared = _session("shared", "10:00:00")
        self._write(base, _doc(shared))
        self._write(ours, _doc(shared, _session("ours", "12:00:00")))
        self._write(theirs, _doc(shared, _session("theirs", "11:00:00")))

        rc = merge_driver._run_driver(str(base), str(ours), str(theirs), "captures/x.json")
        assert rc == 0
        result = json.loads(ours.read_text())
        assert [s["label"] for s in result["sessions"]] == ["shared", "theirs", "ours"]

    def test_run_driver_conflict_returns_nonzero_and_leaves_ours(self, tmp_path):
        base = tmp_path / "base.json"
        ours = tmp_path / "ours.json"
        theirs = tmp_path / "theirs.json"
        a = _session("a", "10:00:00", payload="6101AA")
        self._write(base, _doc(a))
        self._write(ours, _doc(_session("a", "10:00:00", payload="6101BB")))
        self._write(theirs, _doc())
        ours_before = ours.read_text()

        rc = merge_driver._run_driver(str(base), str(ours), str(theirs))
        assert rc != 0
        assert ours.read_text() == ours_before  # untouched → git shows markers

    def test_run_driver_unreadable_input_returns_nonzero(self, tmp_path):
        base = tmp_path / "base.json"
        ours = tmp_path / "ours.json"
        theirs = tmp_path / "theirs.json"
        base.write_text("{ not json")
        self._write(ours, _doc())
        self._write(theirs, _doc())
        assert merge_driver._run_driver(str(base), str(ours), str(theirs)) != 0

    def test_output_is_byte_identical_to_save_format(self, tmp_path):
        # Merged file must be indistinguishable from a normally-written one.
        base = tmp_path / "base.json"
        ours = tmp_path / "ours.json"
        theirs = tmp_path / "theirs.json"
        shared = _session("shared", "10:00:00")
        merged_doc = _doc(shared, _session("theirs", "11:00:00"), _session("ours", "12:00:00"))
        self._write(base, _doc(shared))
        self._write(ours, _doc(shared, _session("ours", "12:00:00")))
        self._write(theirs, _doc(shared, _session("theirs", "11:00:00")))
        merge_driver._run_driver(str(base), str(ours), str(theirs))

        expected = tmp_path / "expected.json"
        capture_io.dump_capture_file(expected, merged_doc)
        assert ours.read_text() == expected.read_text()


# --------------------------------------------------------------------------
# End-to-end: a real git merge with the driver registered
# --------------------------------------------------------------------------


def _git(repo: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(["git", *args], cwd=repo, capture_output=True, text=True, check=True)


@pytest.fixture()
def git_repo(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-q")
    _git(repo, "config", "user.email", "you@example.com")
    _git(repo, "config", "user.name", "Test")
    _git(repo, "config", "commit.gpgsign", "false")
    return repo


class TestGitMergeIntegration:
    def _register_driver(self, repo: Path) -> None:
        # Point the driver at this test's interpreter so no install of `canair`
        # on PATH is needed inside the temp repo.
        import sys

        driver = f'"{sys.executable}" -m canlib.cli captures merge-driver %O %A %B %P'
        _git(repo, "config", "merge.canair-captures.name", "canair session-union merge")
        _git(repo, "config", "merge.canair-captures.driver", driver)

    def _commit_file(self, repo: Path, rel: str, doc) -> None:
        path = repo / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        capture_io.dump_capture_file(path, doc)
        _git(repo, "add", rel)

    def test_same_day_both_append_merges_cleanly(self, git_repo, monkeypatch):
        repo = git_repo
        rel = "profiles/p/captures/2026-07-28.json"

        (repo / ".gitattributes").write_text(
            f"{rel} merge=canair-captures\n".replace("2026-07-28.json", "*.json")
        )
        _git(repo, "add", ".gitattributes")
        self._commit_file(repo, rel, _doc(_session("shared", "10:00:00")))
        _git(repo, "commit", "-qm", "base")

        # Branch A appends one session.
        _git(repo, "checkout", "-qb", "machine-a")
        self._commit_file(
            repo, rel, _doc(_session("shared", "10:00:00"), _session("a", "12:00:00"))
        )
        _git(repo, "commit", "-qm", "machine a append")

        # Branch B appends a different session.
        _git(repo, "checkout", "-q", "master") if _has_branch(repo, "master") else _git(
            repo, "checkout", "-q", "main"
        )
        _git(repo, "checkout", "-qb", "machine-b")
        self._commit_file(
            repo, rel, _doc(_session("shared", "10:00:00"), _session("b", "11:00:00"))
        )
        _git(repo, "commit", "-qm", "machine b append")

        # Register the driver and merge A into B.
        self._register_driver(repo)
        merge = subprocess.run(
            ["git", "merge", "machine-a", "-m", "merge"],
            cwd=repo,
            capture_output=True,
            text=True,
        )
        assert merge.returncode == 0, merge.stderr + merge.stdout

        result = json.loads((repo / rel).read_text())
        assert [s["label"] for s in result["sessions"]] == ["shared", "b", "a"]

    def test_without_driver_conflicts(self, git_repo):
        # Sanity: prove the conflict is real when the driver is NOT registered
        # (so the driver's value is demonstrable, not assumed).
        repo = git_repo
        rel = "profiles/p/captures/2026-07-28.json"
        self._commit_file(repo, rel, _doc(_session("shared", "10:00:00")))
        _git(repo, "commit", "-qm", "base")
        _git(repo, "checkout", "-qb", "a")
        self._commit_file(
            repo, rel, _doc(_session("shared", "10:00:00"), _session("a", "12:00:00"))
        )
        _git(repo, "commit", "-qm", "a")
        base_branch = "main" if _has_branch(repo, "main") else "master"
        _git(repo, "checkout", "-q", base_branch)
        _git(repo, "checkout", "-qb", "b")
        self._commit_file(
            repo, rel, _doc(_session("shared", "10:00:00"), _session("b", "11:00:00"))
        )
        _git(repo, "commit", "-qm", "b")
        merge = subprocess.run(
            ["git", "merge", "a", "-m", "merge"], cwd=repo, capture_output=True, text=True
        )
        assert merge.returncode != 0  # conflicts without the driver


def _has_branch(repo: Path, name: str) -> bool:
    out = subprocess.run(
        ["git", "branch", "--list", name], cwd=repo, capture_output=True, text=True
    )
    return bool(out.stdout.strip())
