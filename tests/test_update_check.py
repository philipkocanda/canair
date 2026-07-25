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

    def test_check_makes_no_changes(self, monkeypatch, capsys):
        from canlib.commands import update as update_cmd

        called = {"git": False}

        def _git(*a, **k):
            called["git"] = True

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
        assert called["git"] is False

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
        assert "git pull --ff-only" in out

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
