"""Tests for the run-length validity model (canlib.fill).

The unit that decides *policy*: which stored rows may be carried forward and how
far. The join mechanism that consumes it is covered in ``test_align.py``
(``TestForwardFill``); the end-to-end command behaviour in the per-command suites.
"""

from datetime import datetime, timedelta

import pytest

from canlib.fill import (
    FILL_AUTO,
    FILL_HOLD,
    FILL_MODES,
    FILL_NONE,
    FORCED_HOLD_WARNING,
    FillPolicy,
    forced_hold_warning,
    format_hold_duration,
    hold_until_vector,
    parse_fill_mode,
    session_end_times,
    session_key,
)

BASE = datetime(2026, 8, 5, 12, 0, 0)


def _entry(*, sec: float, keep="changes", file="2026-08-05.json", session=0):
    return {
        "file": file,
        "_session_idx": session,
        "keep_mode": keep,
        "time": (BASE + timedelta(seconds=sec)).strftime("%H:%M:%S.%f"),
        "date": "2026-08-05",
    }


def _at(sec: float) -> datetime:
    return BASE + timedelta(seconds=sec)


class TestParseFillMode:
    def test_known_modes_round_trip(self):
        assert [parse_fill_mode(m) for m in FILL_MODES] == list(FILL_MODES)

    @pytest.mark.parametrize("bad", ["", "yes", None, 1, "HOLD"])
    def test_unknown_defaults_to_auto(self, bad):
        assert parse_fill_mode(bad) == FILL_AUTO


class TestFillPolicy:
    def test_none_is_disabled(self):
        assert not FillPolicy(mode=FILL_NONE).enabled
        assert FillPolicy(mode=FILL_AUTO).enabled

    def test_auto_allows_only_run_length_rows(self):
        policy = FillPolicy(mode=FILL_AUTO)
        assert policy.allows(_entry(sec=0, keep="changes"))
        assert not policy.allows(_entry(sec=0, keep="unique"))
        assert not policy.allows(_entry(sec=0, keep=""))

    def test_hold_forces_every_row(self):
        policy = FillPolicy(mode=FILL_HOLD)
        assert policy.allows(_entry(sec=0, keep="unique"))
        assert policy.allows(_entry(sec=0, keep=""))

    def test_is_hashable_so_it_can_key_a_cache(self):
        # LoadedPid memoises its hold vector per policy; an unhashable policy would
        # silently defeat that and re-derive the vector for every series.
        assert len({FillPolicy(), FillPolicy(), FillPolicy(max_hold_s=5)}) == 2


class TestSessionEndTimes:
    def test_uses_the_latest_capture_in_each_session(self):
        entries = [_entry(sec=0), _entry(sec=90), _entry(sec=30)]
        ends = session_end_times(entries)
        assert ends[session_key(entries[0])] == _at(90)

    def test_sessions_are_kept_separate(self):
        a = _entry(sec=0, session=0)
        b = _entry(sec=10, session=1)
        ends = session_end_times([a, b])
        assert ends == {session_key(a): _at(0), session_key(b): _at(10)}

    def test_untimed_rows_ignored(self):
        e = _entry(sec=0)
        untimed = dict(e)
        untimed["time"] = ""
        assert session_end_times([untimed]) == {}


class TestHoldUntilVector:
    def _holds(self, entries, *, policy=None):
        policy = policy or FillPolicy()
        dts = [_at(i) for i in range(len(entries))]
        return (
            entries,
            dts,
            hold_until_vector(
                entries,
                dts,
                session_ends=session_end_times(entries),
                policy=policy,
            ),
        )

    def test_each_row_holds_until_the_next(self):
        entries = [_entry(sec=0), _entry(sec=1), _entry(sec=2)]
        dts = [_at(0), _at(1), _at(2)]
        holds = hold_until_vector(
            entries, dts, session_ends=session_end_times(entries), policy=FillPolicy()
        )
        assert holds[0] == _at(1)
        assert holds[1] == _at(2)

    def test_final_row_closes_at_the_session_end_not_its_own_time(self):
        """The evidence a value held is that polling continued and stored nothing."""
        rows = [_entry(sec=0), _entry(sec=1)]
        # Another PID kept the session alive until t=600 — that is when this PID's
        # last run stops being known, not at its own last capture.
        other = _entry(sec=600)
        dts = [_at(0), _at(1)]
        holds = hold_until_vector(
            rows,
            dts,
            session_ends=session_end_times([*rows, other]),
            policy=FillPolicy(),
        )
        assert holds[-1] == _at(600)

    def test_last_row_of_the_session_holds_nothing(self):
        rows = [_entry(sec=0), _entry(sec=1)]
        dts = [_at(0), _at(1)]
        holds = hold_until_vector(
            rows, dts, session_ends=session_end_times(rows), policy=FillPolicy()
        )
        assert holds[-1] is None  # zero-width run — nothing to carry

    def test_never_carries_across_a_session_boundary(self):
        """The ECU may have changed unobserved between two recordings."""
        rows = [_entry(sec=0, session=0), _entry(sec=1, session=1)]
        dts = [_at(0), _at(1)]
        holds = hold_until_vector(
            rows, dts, session_ends=session_end_times(rows), policy=FillPolicy()
        )
        assert holds == [None, None]

    def test_max_hold_caps_the_carry(self):
        rows = [_entry(sec=0), _entry(sec=600)]
        dts = [_at(0), _at(600)]
        holds = hold_until_vector(
            rows,
            dts,
            session_ends=session_end_times(rows),
            policy=FillPolicy(max_hold_s=30),
        )
        assert holds[0] == _at(30)

    def test_input_order_is_preserved_for_unsorted_rows(self):
        """Callers zip this against an unsorted frame list, so order must not shift."""
        rows = [_entry(sec=5), _entry(sec=0), _entry(sec=9)]
        dts = [_at(5), _at(0), _at(9)]
        holds = hold_until_vector(
            rows, dts, session_ends=session_end_times(rows), policy=FillPolicy()
        )
        assert holds[1] == _at(5)  # the t=0 row is closed by the t=5 row
        assert holds[0] == _at(9)

    def test_fill_none_holds_nothing(self):
        rows = [_entry(sec=0), _entry(sec=1)]
        holds = hold_until_vector(
            rows,
            [_at(0), _at(1)],
            session_ends=session_end_times(rows),
            policy=FillPolicy(mode=FILL_NONE),
        )
        assert holds == [None, None]

    def test_mixed_keep_modes_fill_only_the_run_length_rows(self):
        """A scope must not degrade to its weakest session's provenance."""
        rows = [
            _entry(sec=0, keep="changes", session=0),
            _entry(sec=1, keep="changes", session=0),
            _entry(sec=2, keep="unique", session=1),
            _entry(sec=3, keep="unique", session=1),
        ]
        holds = hold_until_vector(
            rows,
            [_at(i) for i in range(4)],
            session_ends=session_end_times(rows),
            policy=FillPolicy(),
        )
        assert holds[0] == _at(1)
        assert holds[2] is None

    def test_empty_input(self):
        assert hold_until_vector([], [], session_ends={}, policy=FillPolicy()) == []


class TestForcedHoldWarning:
    def test_warns_when_forcing_hold_over_unique_data(self):
        assert (
            forced_hold_warning([_entry(sec=0, keep="unique")], FillPolicy(mode=FILL_HOLD))
            == FORCED_HOLD_WARNING
        )

    def test_silent_under_auto(self):
        assert forced_hold_warning([_entry(sec=0, keep="unique")], FillPolicy()) is None

    def test_silent_when_no_unique_data_in_scope(self):
        assert forced_hold_warning([_entry(sec=0)], FillPolicy(mode=FILL_HOLD)) is None


class TestFormatHoldDuration:
    @pytest.mark.parametrize(
        ("seconds", "want"),
        [(0, "0s"), (12, "12s"), (59.4, "59s"), (60, "1m"), (240, "4m"), (10680, "2h58m")],
    )
    def test_readable(self, seconds, want):
        assert format_hold_duration(seconds) == want
