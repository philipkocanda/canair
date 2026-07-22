"""Tests for canlib.timeouts — response-budget resolution."""

from __future__ import annotations

import argparse

from canlib.timeouts import cli_timeout, ecu_timeouts_by_name, ecu_timeouts_by_tx


def _args(**kw):
    return argparse.Namespace(**kw)


def test_cli_timeout_none_when_unset():
    assert cli_timeout(_args(timeout=None)) is None
    assert cli_timeout(_args()) is None  # attribute missing


def test_cli_timeout_returns_seconds():
    assert cli_timeout(_args(timeout=5)) == 5.0
    assert cli_timeout(_args(timeout=0.5)) == 0.5


_PIDS = {
    "ecus": {
        "VCU": {"tx_id": 0x7E2, "response_timeout_ms": 4000},
        "MCU": {"tx_id": 0x7E3, "response_timeout_ms": 3000},
        "ESC": {"tx_id": 0x7D1},  # no per-ECU budget
    }
}


def test_ecu_timeouts_by_tx():
    m = ecu_timeouts_by_tx(_PIDS)
    assert m == {0x7E2: 4.0, 0x7E3: 3.0}  # ESC excluded, ms -> s


def test_ecu_timeouts_by_name():
    m = ecu_timeouts_by_name(_PIDS)
    assert m == {"VCU": 4.0, "MCU": 3.0}


def test_ecu_timeouts_empty_when_none_set():
    assert ecu_timeouts_by_tx({"ecus": {"ESC": {"tx_id": 0x7D1}}}) == {}
    assert ecu_timeouts_by_name({}) == {}
