"""The WiCAN sleep/battery banner printed into a connected live session.

It used to fetch through helpers that called ``sys.exit(1)`` on any HTTP failure,
and that ``SystemExit`` slipped past the banner's ``except Exception`` and ended
an already-connected session (an HTTP timeout right after connecting was enough).
"""

from __future__ import annotations

import pytest
import requests


class FakeResponse:
    def __init__(self, status: int, body=None):
        self.status_code = status
        self._body = body

    def json(self):
        return self._body

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise requests.HTTPError(f"{self.status_code} Client Error")


class TestSleepBannerRegression:
    @pytest.mark.parametrize(
        "error",
        [
            requests.ConnectionError("down"),
            requests.Timeout("slow"),
            requests.HTTPError("404 Client Error"),
        ],
    )
    def test_http_failure_never_ends_the_session(self, monkeypatch, capsys, error):
        """The banner used to sys.exit(1) from inside a connected session."""
        from canlib import wican_api
        from canlib.commands._live.connect import _print_sleep_banner

        def boom(*a, **k):
            raise error

        monkeypatch.setattr(wican_api.requests, "get", boom)
        _print_sleep_banner("192.0.2.1")  # must return, not raise SystemExit
        assert capsys.readouterr().out == ""

    def test_prints_when_the_api_answers(self, monkeypatch, capsys):
        from canlib import wican_api
        from canlib.commands._live.connect import _print_sleep_banner

        bodies = {
            "/check_status": {"batt_voltage": "12.6V", "sta_pass": "hunter2"},
            "/load_config": {"sleep_status": "disable"},
        }

        def get(url, timeout):
            return FakeResponse(200, next(b for p, b in bodies.items() if url.endswith(p)))

        monkeypatch.setattr(wican_api.requests, "get", get)
        _print_sleep_banner("192.0.2.1")
        out = capsys.readouterr().out
        assert "12.6V" in out
        assert "hunter2" not in out
