"""Tests for build provenance (canair.install.build_info).

canair reports and *records* a version that identifies the code that ran, not
just the release it was near: from a git checkout the version carries the branch
and short commit (``1.15.0+main.343b244``). These verify the composition, the
git query behind it, and that it degrades to the plain package version whenever
there's no checkout to describe.
"""

from __future__ import annotations

import ast
import json
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path

import pytest

from canair.install import build_info as bi


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
        import canair

        monkeypatch.setattr(canair, "__version__", "1.2.3")
        assert bi.full_version() == "1.2.3"

    def test_appends_the_checkout_provenance(self, monkeypatch):
        monkeypatch.setattr(
            bi, "running_build", lambda: bi.GitBuild(branch="main", commit="343b244", dirty=False)
        )
        import canair

        monkeypatch.setattr(canair, "__version__", "1.2.3")
        assert bi.full_version() == "1.2.3+main.343b244"

    def test_uses_a_dot_when_the_base_already_has_a_local_segment(self, monkeypatch):
        # The "not installed" sentinel already carries a `+`; a version may hold
        # only one, so the provenance must extend the existing segment.
        monkeypatch.setattr(
            bi, "running_build", lambda: bi.GitBuild(branch="main", commit="343b244", dirty=True)
        )
        import canair

        monkeypatch.setattr(canair, "__version__", "0+unknown")
        assert bi.full_version() == "0+unknown.main.343b244.dirty"

    def test_update_check_still_orders_a_provenance_version(self):
        """A local segment must not confuse the release comparison."""
        from canair.install.update_check import _parse_version, is_newer

        assert _parse_version("1.2.3+main.343b244.dirty") == (1, 2, 3)
        assert is_newer("v1.3.0", "1.2.3+main.343b244.dirty") is True
        assert is_newer("v1.2.3", "1.2.3+main.343b244.dirty") is False


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
        from canair.install import context as ic

        monkeypatch.setattr(ic, "running_origin", lambda: "uv-tool")
        assert bi.running_clone() is None
        assert bi.running_build() is None

    def test_the_clone_containing_the_running_package(self, monkeypatch, tmp_path):
        from canair.install import context as ic

        monkeypatch.setattr(ic, "running_origin", lambda: "repo")
        monkeypatch.setattr(ic, "running_package_dir", lambda: tmp_path / "canair")
        assert bi.running_clone() == tmp_path


class TestReleaseTag:
    def test_a_clean_tree_at_the_release_tag_is_the_release(self):
        build = bi.GitBuild(branch=None, commit="343b244", dirty=False, tag="v1.2.3")
        assert build.is_release_of("1.2.3") is True

    def test_a_dirty_tree_at_the_tag_is_not(self):
        build = bi.GitBuild(branch=None, commit="343b244", dirty=True, tag="v1.2.3")
        assert build.is_release_of("1.2.3") is False

    def test_another_versions_tag_is_not(self):
        build = bi.GitBuild(branch=None, commit="343b244", dirty=False, tag="v1.2.2")
        assert build.is_release_of("1.2.3") is False

    def test_full_version_drops_the_suffix_for_a_release(self, monkeypatch):
        import canair

        monkeypatch.setattr(
            bi,
            "running_build",
            lambda: bi.GitBuild(branch=None, commit="343b244", dirty=False, tag="v1.2.3"),
        )
        monkeypatch.setattr(canair, "__version__", "1.2.3")
        assert bi.full_version() == "1.2.3"

    def test_running_build_looks_up_the_tag_of_a_clean_checkout(self, tmp_path, monkeypatch):
        git = _init_repo(tmp_path)
        git("tag", "v1.2.3")
        monkeypatch.setattr(bi, "running_clone", lambda: tmp_path)
        build = bi.running_build()
        assert build is not None
        assert build.tag == "v1.2.3"

    def test_running_build_skips_the_tag_lookup_for_a_dirty_checkout(self, tmp_path, monkeypatch):
        git = _init_repo(tmp_path)
        (tmp_path / "f.txt").write_text("one\n")
        git("add", "f.txt")
        git("commit", "-q", "-m", "add f")
        git("tag", "v1.2.3")
        (tmp_path / "f.txt").write_text("two\n")
        monkeypatch.setattr(bi, "running_clone", lambda: tmp_path)
        build = bi.running_build()
        assert build is not None
        assert build.dirty is True
        assert build.tag is None


_NOW = datetime(2026, 11, 5, 10, 12, 34, tzinfo=UTC)


class TestCollectStamp:
    """What the build hook (hatch_build.py) writes into a wheel."""

    def test_local_build_of_a_checkout(self, tmp_path):
        git = _init_repo(tmp_path)
        head = git("rev-parse", "HEAD").stdout.strip()
        stamp = bi.collect_stamp(tmp_path, {}, now=_NOW)
        assert stamp == {
            "schema": bi.STAMP_SCHEMA,
            "builder": "local",
            "built_at": "2026-11-05T10:12:34Z",
            "commit": head,
            "branch": "main",
            "tag": None,
            "dirty": False,
            "ci_run_url": None,
        }

    def test_records_the_exact_tag(self, tmp_path):
        git = _init_repo(tmp_path)
        git("tag", "v1.2.3")
        assert bi.collect_stamp(tmp_path, {}, now=_NOW)["tag"] == "v1.2.3"

    def test_github_actions_build_names_its_run(self, tmp_path):
        _init_repo(tmp_path)
        env = {
            "GITHUB_ACTIONS": "true",
            "GITHUB_SERVER_URL": "https://github.com",
            "GITHUB_REPOSITORY": "owner/canair",
            "GITHUB_RUN_ID": "42",
        }
        stamp = bi.collect_stamp(tmp_path, env, now=_NOW)
        assert stamp["builder"] == "ci"
        assert stamp["ci_run_url"] == "https://github.com/owner/canair/actions/runs/42"

    def test_another_ci_is_a_ci_build_without_a_run_link(self, tmp_path):
        _init_repo(tmp_path)
        stamp = bi.collect_stamp(tmp_path, {"CI": "true"}, now=_NOW)
        assert stamp["builder"] == "ci"
        assert stamp["ci_run_url"] is None

    def test_source_date_epoch_sets_the_build_time(self, tmp_path):
        _init_repo(tmp_path)
        stamp = bi.collect_stamp(tmp_path, {"SOURCE_DATE_EPOCH": "0"}, now=_NOW)
        assert stamp["built_at"] == "1970-01-01T00:00:00Z"

    def test_no_checkout_leaves_the_git_fields_empty(self, tmp_path):
        # A wheel built from an sdist has no .git to read.
        stamp = bi.collect_stamp(tmp_path, {}, now=_NOW)
        assert stamp["builder"] == "local"
        assert stamp["commit"] is None
        assert stamp["dirty"] is None

    def test_round_trips_through_parse_stamp(self, tmp_path):
        _init_repo(tmp_path)
        stamp = bi.collect_stamp(tmp_path, {}, now=_NOW)
        parsed = bi.parse_stamp(json.dumps(stamp))
        assert parsed is not None
        assert parsed.commit == stamp["commit"]
        assert parsed.built_at == stamp["built_at"]
        assert parsed.git() == bi.GitBuild(
            branch="main", commit=stamp["commit"][: bi.SHORT_SHA_LEN], dirty=False
        )


class TestParseStamp:
    @pytest.mark.parametrize(
        "text",
        [
            "not json",
            "[]",
            json.dumps({"schema": 999, "builder": "ci"}),
            json.dumps({"schema": bi.STAMP_SCHEMA, "builder": "somebody"}),
        ],
    )
    def test_unusable_stamps_are_ignored(self, text):
        assert bi.parse_stamp(text) is None

    def test_a_stamp_without_git_facts_has_no_git_build(self):
        stamp = bi.parse_stamp(json.dumps({"schema": bi.STAMP_SCHEMA, "builder": "local"}))
        assert stamp is not None
        assert stamp.git() is None


def _stamp_package(monkeypatch, tmp_path, **fields):
    """Make the running copy an installed package whose wheel carries ``fields``."""
    from canair.install import context as ic

    pkg = tmp_path / "canair"
    pkg.mkdir()
    (pkg / bi.STAMP_FILE).write_text(json.dumps({"schema": bi.STAMP_SCHEMA, **fields}))
    monkeypatch.setattr(ic, "running_origin", lambda: "uv-tool")
    monkeypatch.setattr(ic, "running_package_dir", lambda: pkg)


class TestInstalledBuild:
    COMMIT = "343b244" + "a" * 33

    def test_version_of_a_local_build_off_a_branch(self, monkeypatch, tmp_path):
        import canair

        _stamp_package(
            monkeypatch, tmp_path, builder="local", commit=self.COMMIT, branch="main", dirty=False
        )
        monkeypatch.setattr(canair, "__version__", "1.2.3")
        assert bi.full_version() == "1.2.3+main.343b244"
        assert bi.build_origin() == "local"

    def test_version_of_a_release_build(self, monkeypatch, tmp_path):
        import canair

        _stamp_package(
            monkeypatch,
            tmp_path,
            builder="ci",
            commit=self.COMMIT,
            tag="v1.2.3",
            dirty=False,
            built_at="2026-11-05T10:12:34Z",
        )
        monkeypatch.setattr(canair, "__version__", "1.2.3")
        assert bi.full_version() == "1.2.3"
        assert bi.build_summary() == "CI build 343b244, 2026-11-05 10:12 UTC"

    def test_summary_of_a_local_build(self, monkeypatch, tmp_path):
        _stamp_package(
            monkeypatch,
            tmp_path,
            builder="local",
            commit=self.COMMIT,
            built_at="2026-11-05T10:12:34Z",
        )
        assert bi.build_summary() == "local build 343b244, 2026-11-05 10:12 UTC"

    def test_details_carry_the_run_link(self, monkeypatch, tmp_path):
        url = "https://github.com/owner/canair/actions/runs/42"
        _stamp_package(monkeypatch, tmp_path, builder="ci", commit=self.COMMIT, ci_run_url=url)
        details = bi.build_details()
        assert details["origin"] == "ci"
        assert details["commit"] == self.COMMIT
        assert details["ci_run_url"] == url

    def test_an_unstamped_install_says_so(self, monkeypatch, tmp_path):
        import canair
        from canair.install import context as ic

        (tmp_path / "canair").mkdir()
        monkeypatch.setattr(ic, "running_origin", lambda: "other")
        monkeypatch.setattr(ic, "running_package_dir", lambda: tmp_path / "canair")
        monkeypatch.setattr(canair, "__version__", "1.2.3")
        assert bi.full_version() == "1.2.3"
        assert bi.build_origin() == "unknown"
        assert bi.build_summary() == "installed, no build info"

    def test_a_run_from_source_ignores_a_stray_stamp(self, monkeypatch, tmp_path):
        from canair.install import context as ic

        _stamp_package(monkeypatch, tmp_path, builder="ci", commit=self.COMMIT)
        monkeypatch.setattr(ic, "running_origin", lambda: "repo")
        assert bi.running_stamp() is None
        assert bi.build_summary() == "from source"


def test_module_imports_only_the_standard_library():
    """hatch_build.py loads this module by path at build time, with canair not installed."""
    tree = ast.parse(Path(bi.__file__).read_text())
    for node in tree.body:
        if isinstance(node, ast.Import):
            names = [alias.name for alias in node.names]
        elif isinstance(node, ast.ImportFrom):
            names = [node.module or ""]
        else:
            continue
        for name in names:
            top = name.split(".")[0]
            assert top == "__future__" or top in sys.stdlib_module_names, name
