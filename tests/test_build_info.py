"""Tests for build provenance (canlib.build_info).

canair reports and *records* a version that identifies the code that ran, not
just the release it was near: from a git checkout the version carries the branch
and short commit (``1.15.0+main.343b244``). These verify the composition, the
git query behind it, and that it degrades to the plain package version whenever
there's no checkout to describe.
"""

from __future__ import annotations

import subprocess

import pytest

from canlib import build_info as bi


@pytest.fixture(autouse=True)
def _clear_git_cache():
    """The git facts are cached per process; keep tests independent."""
    bi.clear_cache()
    yield
    bi.clear_cache()


def _init_repo(path):
    """Create a real (tiny) git repo at ``path`` and return a `git(*args)` runner."""

    def git(*args):
        return subprocess.run(
            ["git", "-C", str(path), *args],
            check=True,
            capture_output=True,
            text=True,
        )

    git("init", "-q", "-b", "main")
    git("config", "user.email", "t@example.com")
    git("config", "user.name", "Test")
    git("commit", "-q", "--allow-empty", "-m", "initial")
    return git


class TestLocalSegment:
    def test_branch_and_commit(self):
        build = bi.GitBuild(branch="main", commit="343b244", dirty=False)
        assert build.local_segment() == "main.343b244"

    def test_dirty_is_flagged(self):
        build = bi.GitBuild(branch="main", commit="343b244", dirty=True)
        assert build.local_segment() == "main.343b244.dirty"

    def test_detached_head_omits_the_branch_token(self):
        build = bi.GitBuild(branch=None, commit="343b244", dirty=False)
        assert build.local_segment() == "343b244"

    def test_branch_is_sanitized_to_the_pep440_alphabet(self):
        build = bi.GitBuild(branch="feature/nice-thing_2", commit="343b244", dirty=False)
        assert build.local_segment() == "feature.nice.thing.2.343b244"

    def test_branch_of_only_separators_is_dropped(self):
        build = bi.GitBuild(branch="///", commit="343b244", dirty=False)
        assert build.local_segment() == "343b244"


class TestFullVersion:
    def test_plain_package_version_without_a_checkout(self, monkeypatch):
        monkeypatch.setattr(bi, "running_build", lambda: None)
        import canlib

        monkeypatch.setattr(canlib, "__version__", "1.2.3")
        assert bi.full_version() == "1.2.3"

    def test_appends_the_checkout_provenance(self, monkeypatch):
        monkeypatch.setattr(
            bi, "running_build", lambda: bi.GitBuild(branch="main", commit="343b244", dirty=False)
        )
        import canlib

        monkeypatch.setattr(canlib, "__version__", "1.2.3")
        assert bi.full_version() == "1.2.3+main.343b244"

    def test_uses_a_dot_when_the_base_already_has_a_local_segment(self, monkeypatch):
        # The "not installed" sentinel already carries a `+`; a version may hold
        # only one, so the provenance must extend the existing segment.
        monkeypatch.setattr(
            bi, "running_build", lambda: bi.GitBuild(branch="main", commit="343b244", dirty=True)
        )
        import canlib

        monkeypatch.setattr(canlib, "__version__", "0+unknown")
        assert bi.full_version() == "0+unknown.main.343b244.dirty"

    def test_update_check_still_orders_a_provenance_version(self):
        """A local segment must not confuse the release comparison."""
        from canlib.update_check import _is_newer, _parse_version

        assert _parse_version("1.2.3+main.343b244.dirty") == (1, 2, 3)
        assert _is_newer("v1.3.0", "1.2.3+main.343b244.dirty") is True
        assert _is_newer("v1.2.3", "1.2.3+main.343b244.dirty") is False


class TestGitBuild:
    def test_reads_branch_and_short_commit(self, tmp_path):
        git = _init_repo(tmp_path)
        head = git("rev-parse", "HEAD").stdout.strip()

        build = bi.git_build(tmp_path)
        assert build is not None
        assert build.branch == "main"
        assert build.commit == head[: bi.SHORT_SHA_LEN]
        assert build.dirty is False

    def test_modified_tracked_file_is_dirty(self, tmp_path):
        git = _init_repo(tmp_path)
        (tmp_path / "f.txt").write_text("one\n")
        git("add", "f.txt")
        git("commit", "-q", "-m", "add f")
        bi.clear_cache()
        assert bi.git_build(tmp_path).dirty is False

        (tmp_path / "f.txt").write_text("two\n")
        bi.clear_cache()
        assert bi.git_build(tmp_path).dirty is True

    def test_untracked_file_is_not_dirty(self, tmp_path):
        # Untracked files don't make the recorded commit an inaccurate
        # description of the code that ran, and scanning for them costs more
        # than it tells us.
        _init_repo(tmp_path)
        (tmp_path / "scratch.txt").write_text("notes\n")
        assert bi.git_build(tmp_path).dirty is False

    def test_detached_head_has_no_branch(self, tmp_path):
        git = _init_repo(tmp_path)
        git("tag", "v1.2.3")
        git("checkout", "-q", "v1.2.3")
        build = bi.git_build(tmp_path)
        assert build is not None
        assert build.branch is None

    def test_none_when_not_a_repo(self, tmp_path):
        assert bi.git_build(tmp_path) is None

    def test_none_when_git_is_unavailable(self, tmp_path, monkeypatch):
        _init_repo(tmp_path)
        bi.clear_cache()

        def no_git(*a, **kw):
            raise FileNotFoundError("git")

        monkeypatch.setattr(subprocess, "run", no_git)
        assert bi.git_build(tmp_path) is None

    def test_result_is_cached_per_clone(self, tmp_path, monkeypatch):
        """The facts are stable for one invocation; this sits on the capture-save path."""
        _init_repo(tmp_path)
        bi.clear_cache()
        first = bi.git_build(tmp_path)

        def fail(*a, **kw):
            raise AssertionError("git_build shelled out to git a second time")

        monkeypatch.setattr(bi, "run_git", fail)
        assert bi.git_build(tmp_path) == first


class TestIsDirty:
    def test_true_for_a_modified_tracked_file(self, tmp_path):
        git = _init_repo(tmp_path)
        (tmp_path / "f.txt").write_text("one\n")
        git("add", "f.txt")
        git("commit", "-q", "-m", "add f")
        (tmp_path / "f.txt").write_text("two\n")
        assert bi.is_dirty(tmp_path) is True

    def test_false_for_a_clean_tree(self, tmp_path):
        _init_repo(tmp_path)
        assert bi.is_dirty(tmp_path) is False

    def test_false_when_git_cannot_answer(self, tmp_path):
        # Only a *positive* answer blocks `canair update`.
        assert bi.is_dirty(tmp_path) is False


class TestHeadLabel:
    """Branch name on a branch, tag/commit when detached."""

    def test_reports_branch_name(self, tmp_path):
        _init_repo(tmp_path)
        assert bi.head_label(tmp_path) == "main"

    def test_reports_detached_tag(self, tmp_path):
        git = _init_repo(tmp_path)
        git("tag", "v1.2.3")
        git("checkout", "-q", "v1.2.3")
        assert bi.head_label(tmp_path) == "detached at v1.2.3"

    def test_reports_detached_commit_without_tag(self, tmp_path):
        git = _init_repo(tmp_path)
        git("commit", "-q", "--allow-empty", "-m", "second")
        first = git("rev-parse", "HEAD~1").stdout.strip()
        git("checkout", "-q", first)
        head = bi.head_label(tmp_path)
        assert head == f"detached at {first[: bi.SHORT_SHA_LEN]}"

    def test_none_when_not_a_repo(self, tmp_path):
        assert bi.head_label(tmp_path) is None


class TestRunningClone:
    def test_none_for_an_installed_copy(self, monkeypatch):
        from canlib import install_context as ic

        monkeypatch.setattr(ic, "running_origin", lambda: "uv-tool")
        assert bi.running_clone() is None
        assert bi.running_build() is None

    def test_the_clone_containing_the_running_package(self, monkeypatch, tmp_path):
        from canlib import install_context as ic

        monkeypatch.setattr(ic, "running_origin", lambda: "repo")
        monkeypatch.setattr(ic, "running_package_dir", lambda: tmp_path / "canlib")
        assert bi.running_clone() == tmp_path
