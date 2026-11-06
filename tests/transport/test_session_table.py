"""The session state SessionManager acts on (canair.transport.session_table)."""

from __future__ import annotations

from canair.transport.session_table import SessionTable


class _Clock:
    def __init__(self) -> None:
        self.now = 100.0

    def __call__(self) -> float:
        return self.now


def test_a_session_is_tracked_whatever_the_reply_but_its_mode_only_when_accepted():
    table = SessionTable(_Clock())
    table.opened(0x770, 0x03, accepted=False)
    assert table.has(0x770) and table.mode(0x770) is None
    table.opened(0x770, 0x03, accepted=True)
    assert table.mode(0x770) == 0x03
    table.opened(0x770, 0x81, accepted=False)  # a refused switch leaves it where it was
    assert table.mode(0x770) == 0x03


def test_stale_lists_what_was_not_refreshed_within_the_threshold():
    clock = _Clock()
    table = SessionTable(clock)
    table.track(0x770)
    table.track(0x7A5)
    clock.now += 2.0
    table.mark_active(0x7A5)
    assert table.stale(1.5) == [0x770]


def test_mark_active_needs_a_tracked_session():
    table = SessionTable(_Clock())
    table.mark_active(0x770)
    assert not table.has(0x770)


def test_an_ecu_without_tester_present_is_never_due_again():
    clock = _Clock()
    table = SessionTable(clock)
    table.track(0x7E4)
    assert table.refused_keepalive(0x7E4) is True
    assert table.refused_keepalive(0x7E4) is False  # reported once
    clock.now += 10
    assert table.stale(1.5) == []
    assert table.has(0x7E4) and not table.wants_keepalive(0x7E4)


def test_forget_and_tracked_order():
    table = SessionTable(_Clock())
    for tx in (0x770, 0x7A0, 0x7A5):
        table.track(tx)
    table.forget(0x7A0)
    assert table.tracked() == [0x770, 0x7A5]
