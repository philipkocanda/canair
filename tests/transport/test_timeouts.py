"""Tests for canair.transport.timeouts — response-budget resolution."""

from __future__ import annotations

from canair.transport.timeouts import (
    PENDING_RECV_TIMEOUT,
    PENDING_TOTAL_TIMEOUT,
    cli_timeout,
    ecu_timeouts_by_name,
    ecu_timeouts_by_tx,
    pending_deadline,
)


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
