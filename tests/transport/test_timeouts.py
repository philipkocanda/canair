"""Tests for canair.transport.timeouts — response-budget resolution."""

from __future__ import annotations

import asyncio

import pytest

from canair.transport.elm327.terminal import Elm327Terminal
from canair.transport.timeouts import (
    BUSY_RETRY_DELAYS,
    PENDING_RECV_TIMEOUT,
    PENDING_TOTAL_TIMEOUT,
    busy_retry_delay,
    cli_timeout,
    ecu_timeouts_by_name,
    ecu_timeouts_by_tx,
    pending_deadline,
)
from canair.uds.protocol import UDS, UNKNOWN
from tests.support.fakes import QueuedChannel


def test_cli_timeout_none_when_unset():
    assert cli_timeout(None) is None


def test_cli_timeout_returns_seconds():
    assert cli_timeout(5) == 5.0
    assert cli_timeout(0.5) == 0.5


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


class TestPendingDeadline:
    """ResponsePending extends the wait, but a run of them ends at the cap."""

    def test_the_first_pending_frame_starts_the_run(self):
        deadline, cap = pending_deadline(100.0, PENDING_RECV_TIMEOUT, None)
        assert deadline == 100.0 + PENDING_RECV_TIMEOUT
        assert cap == 100.0 + PENDING_TOTAL_TIMEOUT

    def test_a_later_pending_frame_cannot_pass_the_cap(self):
        _, cap = pending_deadline(100.0, PENDING_RECV_TIMEOUT, None)
        deadline, again = pending_deadline(118.0, PENDING_RECV_TIMEOUT, cap)
        assert again == cap
        assert deadline == cap

    def test_a_longer_explicit_wait_is_not_cut_short(self):
        deadline, cap = pending_deadline(0.0, 30.0, None)
        assert deadline == cap == 30.0


BUSY = {"ok": False, "nrc": 0x21}


class TestBusyRetryDelay:
    def test_a_busy_read_is_repeated_with_a_growing_wait(self):
        delays = [busy_retry_delay(BUSY, "22F190", UDS, n) for n in range(4)]
        assert delays == [*BUSY_RETRY_DELAYS, None]

    @pytest.mark.parametrize(
        "request_hex", ["2FBC1003FF", "3101F000", "14FFFFFF", "1101", "10D1", "10C0"]
    )
    def test_a_request_that_acts_is_not_repeated(self, request_hex):
        assert busy_retry_delay(BUSY, request_hex, UDS, 0) is None

    def test_another_nrc_or_an_answer_is_not_repeated(self):
        assert busy_retry_delay({"ok": False, "nrc": 0x22}, "22F190", UDS, 0) is None
        assert busy_retry_delay({"ok": True}, "22F190", UDS, 0) is None

    def test_a_request_that_is_not_hex_is_not_repeated(self):
        assert busy_retry_delay(BUSY, "22F19", UNKNOWN, 0) is None


@pytest.mark.asyncio
async def test_the_elm327_terminal_repeats_a_busy_read(monkeypatch):
    monkeypatch.setattr("canair.transport.timeouts.BUSY_RETRY_DELAYS", (0.0, 0.0, 0.0))
    ch = QueuedChannel("7F 22 21\r>", "62 F1 90 41\r>")
    resp = await asyncio.wait_for(Elm327Terminal(ch).send_uds("22F190", timeout=1.0), 5.0)
    assert resp["ok"] is True
    assert resp["hex"] == "62F19041"
    assert len(ch.sent) == 2


@pytest.mark.asyncio
async def test_the_elm327_terminal_gives_up_after_three_repeats(monkeypatch):
    monkeypatch.setattr("canair.transport.timeouts.BUSY_RETRY_DELAYS", (0.0, 0.0, 0.0))
    ch = QueuedChannel(*["7F 22 21\r>"] * 5)
    resp = await asyncio.wait_for(Elm327Terminal(ch).send_uds("22F190", timeout=1.0), 5.0)
    assert resp["nrc"] == 0x21
    assert len(ch.sent) == 4
