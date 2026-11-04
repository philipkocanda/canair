"""Tests for the per-ECU response log."""

from __future__ import annotations

import logging

import pytest

import canlib.core.eventlog as clog
from canlib.transport import ecu_log


@pytest.fixture
def isolated_log(tmp_path, monkeypatch):
    """Point the central event log (and so the per-ECU logs beside it) at a temp dir."""
    path = tmp_path / "logs" / "canair.log"
    monkeypatch.setattr(clog, "event_log_path", lambda: path)
    return path


class TestEcuLog:
    def test_responses_are_logged_beside_the_event_log(self, isolated_log, monkeypatch):
        monkeypatch.setattr(ecu_log, "_ecu_loggers", {})
        monkeypatch.setattr(ecu_log, "_initialized", True)
        monkeypatch.setattr(ecu_log, "_active_ecu", "BMS")
        monkeypatch.setattr(ecu_log, "_date_str", "2026-11-04")
        try:
            assert ecu_log.ecu_log_dir() == isolated_log.parent / "ecu"
            ecu_log.log_response("2101", "7EC 03 61 01 00")
            for handler in logging.getLogger("can_request.ecu.BMS").handlers:
                handler.flush()
            written = (isolated_log.parent / "ecu" / "BMS-2026-11-04.log").read_text()
            assert "2101 -> 7EC 03 61 01 00" in written
        finally:
            logger = logging.getLogger("can_request.ecu.BMS")
            for handler in list(logger.handlers):
                handler.close()
                logger.removeHandler(handler)
