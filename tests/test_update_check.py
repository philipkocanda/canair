"""Tests for the update checker (canlib.update_check) and `canair update`."""

from __future__ import annotations

import argparse
import json
import time

import pytest

from canlib import update_check


@pytest.fixture(autouse=True)
def _isolate_config(tmp_path, monkeypatch):
    """Point config_dir at a temp dir and clear the update-check opt-out state."""
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    monkeypatch.delenv(update_check.DISABLE_ENV, raising=False)
    from canlib import config

    config.load_config.cache_clear()
    yield
    config.load_config.cache_clear()


class TestVersionCompare:
    def test_parse_strips_v_prefix_and_suffix(self):
        assert update_check._parse_version("v1.2.3") == (1, 2, 3)
        assert update_check._parse_version("1.2.3") == (1, 2, 3)
        assert update_check._parse_version("1.2.3-rc1") == (1, 2, 3)
        assert update_check._parse_version("1.2.3+local") == (1, 2, 3)

    def test_parse_unknown_is_none(self):
        assert update_check._parse_version(None) is None
        assert update_check._parse_version("0+unknown") == (0,)
        assert update_check._parse_version("garbage") is None

    def test_is_newer(self):
        assert update_check._is_newer("v1.2.0", "1.1.0") is True
        assert update_check._is_newer("v1.1.0", "1.1.0") is False
        assert update_check._is_newer("v1.0.0", "1.1.0") is False
        # Malformed / unknown never claims an update is available.
        assert update_check._is_newer(None, "1.1.0") is False
        assert update_check._is_newer("v1.2.0", "0+unknown") is False


class TestFetchOfflineSafe:
    def test_network_error_returns_none(self, monkeypatch):
        import requests

        def _raise(*a, **k):
            raise requests.RequestException("no network")

        monkeypatch.setattr(requests, "get", _raise)
        assert update_check.fetch_latest_release() is None

    def test_timeout_returns_none(self, monkeypatch):
        import requests

        def _raise(*a, **k):
            raise requests.Timeout("slow")

        monkeypatch.setattr(requests, "get", _raise)
        assert update_check.fetch_latest_release() is None

    def test_os_error_returns_none(self, monkeypatch):
        import requests

        def _raise(*a, **k):
            raise OSError("socket blew up")

        monkeypatch.setattr(requests, "get", _raise)
        assert update_check.fetch_latest_release() is None

    def test_success(self, monkeypatch):
        import requests

        class _Resp:
            def raise_for_status(self):
                pass

            def json(self):
                return {"tag_name": "v9.9.9", "html_url": "https://example/rel"}

        monkeypatch.setattr(requests, "get", lambda *a, **k: _Resp())
        result = update_check.fetch_latest_release()
        assert result == {
            "tag": "v9.9.9",
            "url": "https://example/rel",
            "published_at": None,
        }

    def test_malformed_body_returns_none(self, monkeypatch):
        import requests

        class _Resp:
            def raise_for_status(self):
                pass

            def json(self):
                return {"no_tag": True}

        monkeypatch.setattr(requests, "get", lambda *a, **k: _Resp())
        assert update_check.fetch_latest_release() is None


class TestCacheAndDisable:
    def test_disabled_by_env(self, monkeypatch):
        monkeypatch.setenv(update_check.DISABLE_ENV, "1")
        assert update_check.is_disabled() is True
        assert update_check.should_check_now() is False

    def test_disabled_by_config(self):
        from canlib import config

        config.set_config_key("check_for_updates", False)
        config.load_config.cache_clear()
        assert update_check.is_disabled() is True
        assert update_check.should_check_now() is False

    def test_should_check_when_no_cache(self):
        assert update_check.should_check_now() is True

    def test_should_not_check_when_cache_fresh(self):
        update_check.write_cache("v1.0.0", "https://example")
        assert update_check.should_check_now() is False

    def test_should_check_when_cache_stale(self):
        update_check.write_cache("v1.0.0", "https://example")
        cache = update_check.read_cache()
        cache["checked_at"] = time.time() - (update_check.DEFAULT_INTERVAL + 10)
        update_check._cache_file().write_text(json.dumps(cache))
        assert update_check.should_check_now() is True

    def test_corrupt_cache_is_ignored(self):
        update_check._cache_file().parent.mkdir(parents=True, exist_ok=True)
        update_check._cache_file().write_text("{not json")
        assert update_check.read_cache() is None
        assert update_check.should_check_now() is True


class TestPendingNotice:
    def test_notice_when_newer(self, monkeypatch):
        monkeypatch.setattr(update_check, "__version__", "1.0.0", raising=False)
        # __version__ lives on the package, not the module — patch the source.
        import canlib

        monkeypatch.setattr(canlib, "__version__", "1.0.0")
        update_check.write_cache("v2.0.0", "https://example/changelog")
        notice = update_check.pending_notice()
        assert notice is not None
        assert "v2.0.0" in notice
        assert "canair update" in notice
        assert "https://example/changelog" in notice

    def test_no_notice_when_up_to_date(self, monkeypatch):
        import canlib

        monkeypatch.setattr(canlib, "__version__", "2.0.0")
        update_check.write_cache("v2.0.0", "https://example")
        assert update_check.pending_notice() is None

    def test_no_notice_without_cache(self):
        assert update_check.pending_notice() is None


class TestGitHead:
    """The clone HEAD describer: branch name on a branch, tag/commit when detached."""

    def _init_repo(self, path):
        import subprocess

        def git(*a):
            subprocess.run(
                ["git", "-C", str(path), *a],
                check=True,
                capture_output=True,
                text=True,
            )

        git("init", "-q", "-b", "main")
        git("config", "user.email", "t@example.com")
        git("config", "user.name", "Test")
        git("commit", "-q", "--allow-empty", "-m", "initial")
        return git

    def test_reports_branch_name(self, tmp_path):
        from canlib.commands import update as update_cmd

        self._init_repo(tmp_path)
        assert update_cmd._git_head(tmp_path) == "main"

    def test_reports_detached_tag(self, tmp_path):
        from canlib.commands import update as update_cmd

        git = self._init_repo(tmp_path)
        git("tag", "v1.2.3")
        git("checkout", "-q", "v1.2.3")
        assert update_cmd._git_head(tmp_path) == "detached at v1.2.3"

    def test_reports_detached_commit_without_tag(self, tmp_path):
        import subprocess

        from canlib.commands import update as update_cmd

        git = self._init_repo(tmp_path)
        git("commit", "-q", "--allow-empty", "-m", "second")
        # Detach onto the first commit, which carries no tag.
        first = subprocess.run(
            ["git", "-C", str(tmp_path), "rev-parse", "HEAD~1"],
            check=True,
            capture_output=True,
            text=True,
        ).stdout.strip()
        git("checkout", "-q", first)
        head = update_cmd._git_head(tmp_path)
        assert head is not None
        assert head.startswith("detached at ")

    def test_none_when_not_a_repo(self, tmp_path):
        from canlib.commands import update as update_cmd

        assert update_cmd._git_head(tmp_path) is None


class TestUpdateCommand:
    def _args(self, **kw):
        base = {"check": False, "yes": False, "json": False}
        base.update(kw)
        return argparse.Namespace(**base)

    def test_json_reports_versions(self, monkeypatch, capsys):
        from canlib.commands import update as update_cmd

        monkeypatch.setattr(
            update_cmd,
            "fetch_latest_release",
            lambda *a, **k: {"tag": "v9.9.9", "url": "https://example/rel"},
        )
        import canlib

        monkeypatch.setattr(canlib, "__version__", "1.0.0")
        monkeypatch.setattr(update_cmd, "_find_clone_dir", lambda: None)

        rc = update_cmd.run(self._args(json=True))
        assert rc == 0
        out = json.loads(capsys.readouterr().out)
        assert out["current"] == "1.0.0"
        assert out["latest"] == "v9.9.9"
        assert out["update_available"] is True
        assert out["clone_dir"] is None
        assert out["clone_head"] is None

    def test_json_reports_clone_head(self, monkeypatch, capsys, tmp_path):
        from canlib.commands import update as update_cmd

        monkeypatch.setattr(
            update_cmd,
            "fetch_latest_release",
            lambda *a, **k: {"tag": "v9.9.9", "url": "https://example/rel"},
        )
        import canlib

        monkeypatch.setattr(canlib, "__version__", "1.0.0")
        monkeypatch.setattr(update_cmd, "_find_clone_dir", lambda: tmp_path)
        monkeypatch.setattr(update_cmd, "_git_head", lambda clone: "main")

        rc = update_cmd.run(self._args(json=True))
        assert rc == 0
        out = json.loads(capsys.readouterr().out)
        assert out["clone_head"] == "main"

    def test_shows_branch_in_console(self, monkeypatch, capsys, tmp_path):
        """The clone's current branch is surfaced in the human-readable report."""
        from canlib.commands import update as update_cmd

        monkeypatch.setattr(
            update_cmd,
            "fetch_latest_release",
            lambda *a, **k: {"tag": "v9.9.9", "url": "https://example/rel"},
        )
        import canlib

        monkeypatch.setattr(canlib, "__version__", "1.0.0")
        monkeypatch.setattr(update_cmd, "_find_clone_dir", lambda: tmp_path)
        monkeypatch.setattr(update_cmd, "_git_head", lambda clone: "main")
        monkeypatch.setattr(update_cmd, "_git_dirty", lambda clone: False)
        monkeypatch.setattr(update_cmd.shutil, "which", lambda name: None)

        rc = update_cmd.run(self._args(check=True))
        assert rc == 0
        out = capsys.readouterr().out
        assert "on:" in out
        assert "main" in out

    def test_check_makes_no_changes(self, monkeypatch, capsys):
        from canlib.commands import update as update_cmd

        # --check may issue read-only git queries (e.g. reading the clone's HEAD)
        # but must never run a mutating command (fetch/checkout).
        git_args: list[tuple[str, ...]] = []

        class _CP:
            returncode = 0
            stdout = "main"
            stderr = ""

        def _git(clone, *a):
            git_args.append(a)
            return _CP()

        monkeypatch.setattr(update_cmd, "_git", _git)
        monkeypatch.setattr(
            update_cmd,
            "fetch_latest_release",
            lambda *a, **k: {"tag": "v9.9.9", "url": "https://example/rel"},
        )
        import canlib

        monkeypatch.setattr(canlib, "__version__", "1.0.0")

        rc = update_cmd.run(self._args(check=True))
        assert rc == 0
        mutating = {"fetch", "checkout", "pull", "reset", "merge"}
        assert not any(a and a[0] in mutating for a in git_args)

    def test_no_clone_prints_manual_instructions(self, monkeypatch, capsys):
        from canlib.commands import update as update_cmd

        monkeypatch.setattr(
            update_cmd,
            "fetch_latest_release",
            lambda *a, **k: {"tag": "v9.9.9", "url": "https://example/rel"},
        )
        import canlib

        monkeypatch.setattr(canlib, "__version__", "1.0.0")
        monkeypatch.setattr(update_cmd, "_find_clone_dir", lambda: None)

        rc = update_cmd.run(self._args(yes=True))
        assert rc == update_cmd._CANNOT
        out = capsys.readouterr().out
        assert "git checkout" in out
        assert "git fetch --tags" in out

    def test_offline_still_offers_update(self, monkeypatch, capsys):
        from canlib.commands import update as update_cmd

        monkeypatch.setattr(update_cmd, "fetch_latest_release", lambda *a, **k: None)
        import canlib

        monkeypatch.setattr(canlib, "__version__", "1.0.0")
        monkeypatch.setattr(update_cmd, "_find_clone_dir", lambda: None)

        rc = update_cmd.run(self._args(check=True))
        assert rc == 0
        out = capsys.readouterr().out
        assert "could not reach GitHub" in out

    def test_offline_refuses_to_update(self, monkeypatch, capsys, tmp_path):
        """Without a known release tag there is nothing to check out; refuse."""
        from canlib.commands import update as update_cmd

        monkeypatch.setattr(update_cmd, "fetch_latest_release", lambda *a, **k: None)
        import canlib

        monkeypatch.setattr(canlib, "__version__", "1.0.0")
        monkeypatch.setattr(update_cmd, "_find_clone_dir", lambda: tmp_path)
        monkeypatch.setattr(update_cmd, "_git_head", lambda clone: "main")

        git_calls: list[tuple[str, ...]] = []
        monkeypatch.setattr(
            update_cmd,
            "_git",
            lambda clone, *a: git_calls.append(a),
        )

        rc = update_cmd.run(self._args(yes=True))
        assert rc == update_cmd._CANNOT
        assert git_calls == []  # never ran a git command without a tag

    def test_checks_out_release_tag(self, monkeypatch, capsys, tmp_path):
        """The happy path fetches tags and checks out the advertised release tag."""
        from canlib.commands import update as update_cmd

        monkeypatch.setattr(
            update_cmd,
            "fetch_latest_release",
            lambda *a, **k: {"tag": "v9.9.9", "url": "https://example/rel"},
        )
        import canlib

        monkeypatch.setattr(canlib, "__version__", "1.0.0")
        monkeypatch.setattr(update_cmd, "_find_clone_dir", lambda: tmp_path)
        monkeypatch.setattr(update_cmd, "_git_dirty", lambda clone: False)
        monkeypatch.setattr(update_cmd.shutil, "which", lambda name: "/usr/bin/uv")

        git_calls: list[tuple[str, ...]] = []

        class _CP:
            returncode = 0
            stdout = ""
            stderr = ""

        def _git(clone, *a):
            git_calls.append(a)
            return _CP()

        monkeypatch.setattr(update_cmd, "_git", _git)

        install_calls: list[list[str]] = []

        def _run(cmd, *a, **k):
            install_calls.append(cmd)
            return _CP()

        monkeypatch.setattr(update_cmd.subprocess, "run", _run)

        rc = update_cmd.run(self._args(yes=True))
        assert rc == 0

        # It fetched tags and checked out the release tag — never a branch pull.
        assert ("fetch", "--tags", "--force") in git_calls
        assert ("checkout", "v9.9.9") in git_calls
        assert not any(a[:1] == ("pull",) for a in git_calls)
        # And reinstalled the tool from the clone.
        assert any("install" in cmd and "--reinstall" in cmd for cmd in install_calls)

    def test_checkout_failure_reports(self, monkeypatch, capsys, tmp_path):
        from canlib.commands import update as update_cmd

        monkeypatch.setattr(
            update_cmd,
            "fetch_latest_release",
            lambda *a, **k: {"tag": "v9.9.9", "url": "https://example/rel"},
        )
        import canlib

        monkeypatch.setattr(canlib, "__version__", "1.0.0")
        monkeypatch.setattr(update_cmd, "_find_clone_dir", lambda: tmp_path)
        monkeypatch.setattr(update_cmd, "_git_dirty", lambda clone: False)
        monkeypatch.setattr(update_cmd.shutil, "which", lambda name: "/usr/bin/uv")

        class _CP:
            def __init__(self, rc):
                self.returncode = rc
                self.stdout = ""
                self.stderr = "no such tag" if rc else ""

        def _git(clone, *a):
            # fetch succeeds, checkout fails.
            return _CP(0 if a[:1] == ("fetch",) else 1)

        monkeypatch.setattr(update_cmd, "_git", _git)

        rc = update_cmd.run(self._args(yes=True))
        assert rc == update_cmd._FAILED
        assert "git checkout failed" in capsys.readouterr().out

    def _install(self, **kw):
        base = {
            "running_origin": "uv-tool",
            "running_version": "1.0.0",
            "running_package_dir": "/tmp/canlib",
            "clone_dir": None,
            "clone_version": "1.1.0",
            "tool_install_dir": "/tmp/uvtool",
            "tool_version": "1.0.0",
            "out_of_sync": True,
        }
        base.update(kw)
        return base

    def test_out_of_sync_reinstalls_without_new_release(
        self, monkeypatch, capsys, tmp_path
    ):
        """No newer release, but the tool copy drifted from the clone -> resync."""
        from canlib.commands import update as update_cmd

        # Latest release equals the running version, so nothing to check out.
        monkeypatch.setattr(
            update_cmd,
            "fetch_latest_release",
            lambda *a, **k: {"tag": "v1.0.0", "url": "https://example/rel"},
        )
        import canlib

        monkeypatch.setattr(canlib, "__version__", "1.0.0")
        monkeypatch.setattr(update_cmd, "_find_clone_dir", lambda: tmp_path)
        monkeypatch.setattr(update_cmd, "_git_head", lambda clone: "main")
        monkeypatch.setattr(
            update_cmd,
            "describe_install",
            lambda clone: self._install(clone_dir=str(clone)),
        )
        monkeypatch.setattr(update_cmd.shutil, "which", lambda name: "/usr/bin/uv")

        git_calls: list[tuple[str, ...]] = []
        monkeypatch.setattr(
            update_cmd, "_git", lambda clone, *a: git_calls.append(a)
        )

        install_calls: list[list[str]] = []

        class _CP:
            returncode = 0
            stdout = ""
            stderr = ""

        def _run(cmd, *a, **k):
            install_calls.append(cmd)
            return _CP()

        monkeypatch.setattr(update_cmd.subprocess, "run", _run)

        rc = update_cmd.run(self._args(yes=True))
        assert rc == 0
        # It resynced without touching git (no fetch/checkout).
        assert git_calls == [] or all(
            a[:1] not in {("fetch",), ("checkout",)} for a in git_calls
        )
        # And reinstalled the tool from the clone.
        assert any("install" in cmd and "--reinstall" in cmd for cmd in install_calls)
        out = capsys.readouterr().out
        assert "out of sync" in out

    def test_out_of_sync_no_uv_reports_manual(self, monkeypatch, capsys, tmp_path):
        from canlib.commands import update as update_cmd

        monkeypatch.setattr(
            update_cmd,
            "fetch_latest_release",
            lambda *a, **k: {"tag": "v1.0.0", "url": "https://example/rel"},
        )
        import canlib

        monkeypatch.setattr(canlib, "__version__", "1.0.0")
        monkeypatch.setattr(update_cmd, "_find_clone_dir", lambda: tmp_path)
        monkeypatch.setattr(update_cmd, "_git_head", lambda clone: "main")
        monkeypatch.setattr(
            update_cmd,
            "describe_install",
            lambda clone: self._install(clone_dir=str(clone)),
        )
        monkeypatch.setattr(update_cmd.shutil, "which", lambda name: None)

        rc = update_cmd.run(self._args(yes=True))
        assert rc == update_cmd._CANNOT
        out = capsys.readouterr().out
        assert "uv tool install" in out
        assert "--reinstall" in out

    def test_up_to_date_and_in_sync_does_nothing(self, monkeypatch, capsys, tmp_path):
        from canlib.commands import update as update_cmd

        monkeypatch.setattr(
            update_cmd,
            "fetch_latest_release",
            lambda *a, **k: {"tag": "v1.0.0", "url": "https://example/rel"},
        )
        import canlib

        monkeypatch.setattr(canlib, "__version__", "1.0.0")
        monkeypatch.setattr(update_cmd, "_find_clone_dir", lambda: tmp_path)
        monkeypatch.setattr(update_cmd, "_git_head", lambda clone: "main")
        monkeypatch.setattr(
            update_cmd,
            "describe_install",
            lambda clone: self._install(
                clone_dir=str(clone), clone_version="1.0.0", out_of_sync=False
            ),
        )

        called: list[str] = []
        monkeypatch.setattr(
            update_cmd.subprocess, "run", lambda *a, **k: called.append("run")
        )

        rc = update_cmd.run(self._args(yes=True))
        assert rc == 0
        assert called == []
        assert "Already up to date" in capsys.readouterr().out
