"""Tests for decode/captures shared scoping (date/state/label, first/last) and
the redesigned --compact renderer + --group-by state stats."""

from datetime import date, datetime

import pytest

from canlib.capture_dates import (
    entry_date,
    entry_datetime,
    filter_by_date_range,
    filter_by_text,
    parse_iso_date,
    parse_iso_datetime,
    resolve_date_bounds,
    resolve_scope_bounds,
)
from canlib.commands.decode import query, render

# ---------------------------------------------------------------------------
# capture_dates helpers
# ---------------------------------------------------------------------------


class TestParseIsoDate:
    def test_valid(self):
        assert parse_iso_date("2026-07-22") == date(2026, 7, 22)

    def test_whitespace_trimmed(self):
        assert parse_iso_date("  2026-07-22 ") == date(2026, 7, 22)

    @pytest.mark.parametrize("bad", ["2026/07/22", "not-a-date", "2026-13-01"])
    def test_invalid_raises_argparse_error(self, bad):
        import argparse

        with pytest.raises(argparse.ArgumentTypeError):
            parse_iso_date(bad)


class TestEntryDate:
    def test_plain(self):
        assert entry_date({"date": "2026-07-22"}) == date(2026, 7, 22)

    def test_suffix_falls_back_to_leading_10(self):
        assert entry_date({"date": "2026-04-17-b"}) == date(2026, 4, 17)

    def test_missing_is_none(self):
        assert entry_date({}) is None
        assert entry_date({"date": ""}) is None


class TestEntryDatetime:
    def test_seconds(self):
        dt = entry_datetime({"date": "2026-07-22", "time": "09:02:19"})
        assert dt == datetime(2026, 7, 22, 9, 2, 19)

    def test_milliseconds(self):
        dt = entry_datetime({"date": "2026-07-22", "time": "12:14:52.432"})
        assert dt == datetime(2026, 7, 22, 12, 14, 52, 432000)

    def test_hh_mm_only(self):
        dt = entry_datetime({"date": "2026-07-22", "time": "09:02"})
        assert dt == datetime(2026, 7, 22, 9, 2, 0)

    def test_date_suffix_still_combines(self):
        dt = entry_datetime({"date": "2026-04-17-b", "time": "17:43:35"})
        assert dt == datetime(2026, 4, 17, 17, 43, 35)

    def test_missing_time_is_none(self):
        assert entry_datetime({"date": "2026-07-22"}) is None
        assert entry_datetime({"date": "2026-07-22", "time": ""}) is None

    def test_missing_date_is_none(self):
        assert entry_datetime({"time": "09:02:19"}) is None

    def test_garbage_time_is_none(self):
        assert entry_datetime({"date": "2026-07-22", "time": "NO DATA"}) is None


class TestFilterByDateRange:
    def _entries(self):
        return [
            {"date": "2026-07-20", "id": 1},
            {"date": "2026-07-21", "id": 2},
            {"date": "2026-07-22", "id": 3},
            {"date": "", "id": 4},  # undated
        ]

    def test_no_bounds_returns_all(self):
        e = self._entries()
        assert filter_by_date_range(e) is e

    def test_since_inclusive(self):
        out = filter_by_date_range(self._entries(), since=date(2026, 7, 21))
        assert [x["id"] for x in out] == [2, 3]

    def test_until_inclusive(self):
        out = filter_by_date_range(self._entries(), until=date(2026, 7, 21))
        assert [x["id"] for x in out] == [1, 2]

    def test_range(self):
        out = filter_by_date_range(
            self._entries(), since=date(2026, 7, 21), until=date(2026, 7, 21)
        )
        assert [x["id"] for x in out] == [2]

    def test_undated_dropped_when_bound_active(self):
        out = filter_by_date_range(self._entries(), since=date(2026, 1, 1))
        assert all(x["id"] != 4 for x in out)


class TestParseIsoDatetime:
    def test_date_only_returns_date(self):
        assert parse_iso_datetime("2026-07-22") == date(2026, 7, 22)
        assert not isinstance(parse_iso_datetime("2026-07-22"), datetime)

    def test_hh_mm(self):
        assert parse_iso_datetime("2026-07-22 09:02") == datetime(2026, 7, 22, 9, 2)

    def test_hh_mm_ss(self):
        assert parse_iso_datetime("2026-07-22 09:02:19") == datetime(2026, 7, 22, 9, 2, 19)

    def test_microseconds(self):
        assert parse_iso_datetime("2026-07-22 09:02:19.123456") == datetime(
            2026, 7, 22, 9, 2, 19, 123456
        )

    def test_iso_t_separator(self):
        assert parse_iso_datetime("2026-07-22T09:02:19") == datetime(2026, 7, 22, 9, 2, 19)

    def test_whitespace_trimmed(self):
        assert parse_iso_datetime("  2026-07-22T09:02  ") == datetime(2026, 7, 22, 9, 2)

    @pytest.mark.parametrize("bad", ["2026/07/22", "not-a-date", "2026-07-22 25:00"])
    def test_invalid_raises_argparse_error(self, bad):
        import argparse

        with pytest.raises(argparse.ArgumentTypeError):
            parse_iso_datetime(bad)


class TestFilterByDateRangeTimeAware:
    def _entries(self):
        return [
            {"date": "2026-07-22", "time": "08:00:00", "id": 1},
            {"date": "2026-07-22", "time": "12:30:00", "id": 2},
            {"date": "2026-07-22", "time": "18:15:30", "id": 3},
            {"date": "2026-07-22", "id": 4},  # untimed
        ]

    def test_since_datetime_narrows_within_day(self):
        out = filter_by_date_range(self._entries(), since=datetime(2026, 7, 22, 12, 0))
        assert [x["id"] for x in out] == [2, 3]

    def test_until_datetime_inclusive(self):
        out = filter_by_date_range(self._entries(), until=datetime(2026, 7, 22, 12, 30))
        assert [x["id"] for x in out] == [1, 2]

    def test_datetime_window(self):
        out = filter_by_date_range(
            self._entries(),
            since=datetime(2026, 7, 22, 9, 0),
            until=datetime(2026, 7, 22, 18, 0),
        )
        assert [x["id"] for x in out] == [2]

    def test_untimed_dropped_in_time_aware_mode(self):
        out = filter_by_date_range(self._entries(), since=datetime(2026, 7, 22, 0, 0))
        assert all(x["id"] != 4 for x in out)

    def test_microsecond_precision(self):
        entries = [
            {"date": "2026-07-22", "time": "09:00:00.100000", "id": 1},
            {"date": "2026-07-22", "time": "09:00:00.500000", "id": 2},
        ]
        out = filter_by_date_range(entries, since=datetime(2026, 7, 22, 9, 0, 0, 300000))
        assert [x["id"] for x in out] == [2]

    def test_mixed_date_and_datetime_bounds(self):
        # since date-only (start of day) + until datetime -> time-aware.
        out = filter_by_date_range(
            self._entries(),
            since=date(2026, 7, 22),
            until=datetime(2026, 7, 22, 12, 30),
        )
        assert [x["id"] for x in out] == [1, 2]


class TestResolveDateBoundsTimestamps:
    def _args(self, **kw):
        import argparse

        ns = argparse.Namespace(date=None, since=None, until=None)
        for k, v in kw.items():
            setattr(ns, k, v)
        return ns

    def test_datetime_ordering_ok(self):
        since, _until, err = resolve_date_bounds(
            self._args(
                since=datetime(2026, 7, 22, 9, 0),
                until=datetime(2026, 7, 22, 17, 0),
            )
        )
        assert err is None
        assert since == datetime(2026, 7, 22, 9, 0)

    def test_datetime_reversed_errors(self):
        _, _, err = resolve_date_bounds(
            self._args(
                since=datetime(2026, 7, 22, 17, 0),
                until=datetime(2026, 7, 22, 9, 0),
            )
        )
        assert err is not None

    def test_same_day_date_until_after_since_datetime_ok(self):
        # until is a whole day (end-of-day) so a same-day since time is fine.
        _, _, err = resolve_date_bounds(
            self._args(since=datetime(2026, 7, 22, 17, 0), until=date(2026, 7, 22))
        )
        assert err is None


class TestResolveScopeBounds:
    def _args(self, **kw):
        import argparse

        ns = argparse.Namespace(
            date=None,
            since=None,
            until=None,
            today=False,
            last_sessions=None,
            state=None,
            label=None,
        )
        for k, v in kw.items():
            setattr(ns, k, v)
        return ns

    def test_today_sets_whole_day(self):
        since, until, err = resolve_scope_bounds(self._args(today=True))
        assert err is None
        assert since == date.today()
        assert until == date.today()

    def test_today_conflicts_with_since(self):
        _, _, err = resolve_scope_bounds(self._args(today=True, since=date(2026, 1, 1)))
        assert err is not None

    def test_no_flags_passthrough(self):
        since, until, err = resolve_scope_bounds(self._args())
        assert (since, until, err) == (None, None, None)

    def test_last_sessions_cutoff(self, monkeypatch, tmp_path):
        # Two sessions on different days; --last-session picks the later start.
        entries = [
            {"file": "a.yaml", "_session_idx": 0, "date": "2026-07-20", "time": "08:00:00"},
            {"file": "a.yaml", "_session_idx": 0, "date": "2026-07-20", "time": "08:05:00"},
            {"file": "b.yaml", "_session_idx": 0, "date": "2026-07-22", "time": "14:00:00"},
            {"file": "b.yaml", "_session_idx": 0, "date": "2026-07-22", "time": "14:10:00"},
        ]
        monkeypatch.setattr("canlib.capture_dates.load_all_captures", lambda *a, **k: entries)
        since, _until, err = resolve_scope_bounds(self._args(last_sessions=1))
        assert err is None
        assert since == datetime(2026, 7, 22, 14, 0, 0)
        # Last 2 sessions -> cutoff at the earlier session's start.
        since2, _, _ = resolve_scope_bounds(self._args(last_sessions=2))
        assert since2 == datetime(2026, 7, 20, 8, 0, 0)

    def test_last_sessions_more_than_available(self, monkeypatch):
        entries = [
            {"file": "a.yaml", "_session_idx": 0, "date": "2026-07-20", "time": "08:00:00"},
        ]
        monkeypatch.setattr("canlib.capture_dates.load_all_captures", lambda *a, **k: entries)
        since, _, err = resolve_scope_bounds(self._args(last_sessions=5))
        assert err is None
        assert since == datetime(2026, 7, 20, 8, 0, 0)


class TestFilterByText:
    def _entries(self):
        return [
            {"vehicle_states": ["driving"], "session_label": "ESC drive", "label": ""},
            {"vehicle_states": ["driving"], "session_label": "ESC city", "label": ""},
            {"vehicle_states": ["ready", "parked"], "session_label": "reference", "label": "cap-x"},
        ]

    def test_no_filter_returns_all(self):
        e = self._entries()
        assert filter_by_text(e) is e

    def test_state_substring_case_insensitive(self):
        out = filter_by_text(self._entries(), state="PARK")
        assert len(out) == 1 and out[0]["vehicle_states"] == ["ready", "parked"]

    def test_state_matches_multiple(self):
        out = filter_by_text(self._entries(), state="driving")
        assert len(out) == 2

    def test_label_matches_session_label(self):
        out = filter_by_text(self._entries(), label="city")
        assert len(out) == 1 and out[0]["session_label"] == "ESC city"

    def test_label_matches_capture_label(self):
        out = filter_by_text(self._entries(), label="cap-x")
        assert len(out) == 1 and out[0]["vehicle_states"] == ["ready", "parked"]

    def test_state_and_label_anded(self):
        out = filter_by_text(self._entries(), state="driving", label="city")
        assert len(out) == 1


class _Args:
    def __init__(self, **kw):
        self.date = kw.get("date")
        self.since = kw.get("since")
        self.until = kw.get("until")


class TestResolveDateBounds:
    def test_none(self):
        assert resolve_date_bounds(_Args()) == (None, None, None)

    def test_date_shorthand(self):
        since, until, err = resolve_date_bounds(_Args(date=date(2026, 7, 22)))
        assert since == until == date(2026, 7, 22) and err is None

    def test_date_conflicts_with_since(self):
        _, _, err = resolve_date_bounds(_Args(date=date(2026, 7, 22), since=date(2026, 7, 1)))
        assert err and "cannot be combined" in err

    def test_since_after_until(self):
        _, _, err = resolve_date_bounds(_Args(since=date(2026, 7, 22), until=date(2026, 7, 1)))
        assert err and "after" in err


# ---------------------------------------------------------------------------
# decode.scope_captures (date/text + first/last slicing)
# ---------------------------------------------------------------------------


def _caps():
    return [
        {"date": "2026-07-22", "vehicle_states": ["driving"], "label": "", "id": i}
        for i in range(5)
    ] + [
        {"date": "2026-07-22", "vehicle_states": ["parked"], "label": "", "id": 5},
    ]


class TestScopeCaptures:
    def test_no_scope_returns_all(self):
        out = query.scope_captures(_caps())
        assert len(out) == 6

    def test_state_filter(self):
        out = query.scope_captures(_caps(), state="driving")
        assert len(out) == 5 and all("driving" in c["vehicle_states"] for c in out)

    def test_first(self):
        out = query.scope_captures(_caps(), first=2)
        assert [c["id"] for c in out] == [0, 1]

    def test_last(self):
        out = query.scope_captures(_caps(), last=2)
        assert [c["id"] for c in out] == [4, 5]

    def test_last_zero_is_empty(self):
        assert query.scope_captures(_caps(), last=0) == []

    def test_state_then_last(self):
        out = query.scope_captures(_caps(), state="driving", last=2)
        assert [c["id"] for c in out] == [3, 4]

    def test_date_filters(self):
        caps = [
            *_caps(),
            {"date": "2026-07-20", "vehicle_states": ["parked"], "label": "", "id": 9},
        ]
        out = query.scope_captures(caps, since=date(2026, 7, 22))
        assert all(c["date"] == "2026-07-22" for c in out)


# ---------------------------------------------------------------------------
# Redesigned --compact renderer
# ---------------------------------------------------------------------------


def _compact_results(rows):
    """rows = list of (time, state, {param: value}); build all_results shape."""
    out = []
    for t, state, params in rows:
        decoded = {n: {"value": v, "unit": "", "verified": False} for n, v in params.items()}
        out.append(
            {
                "capture": {
                    "date": "2026-07-22",
                    "time": t,
                    "vehicle_states": [state] if state else [],
                },
                "decoded": decoded,
            }
        )
    return out


class TestPrintCompact:
    def test_header_printed_once(self, capsys):
        results = _compact_results(
            [
                ("16:00:00", "drive", {"SPEED": 0}),
                ("16:00:01", "drive", {"SPEED": 10}),
            ]
        )
        render.print_compact(results, ["SPEED"], {"SPEED": {"unit": "km/h"}}, set())
        out = capsys.readouterr().out
        # Param name appears once (header), NOT on every data row.
        assert out.count("SPEED") == 1
        assert "SPEED[km/h]" in out

    def test_state_divider_only_on_change(self, capsys):
        results = _compact_results(
            [
                ("16:00:00", "drive A", {"SPEED": 0}),
                ("16:00:01", "drive A", {"SPEED": 5}),
                ("16:00:02", "drive B", {"SPEED": 8}),
            ]
        )
        render.print_compact(results, ["SPEED"], {"SPEED": {"unit": ""}}, set())
        out = capsys.readouterr().out
        assert out.count("[drive A]") == 1
        assert out.count("[drive B]") == 1

    def test_changes_only_collapses_repeats(self, capsys):
        results = _compact_results(
            [
                ("16:00:00", "s", {"SPEED": 0}),
                ("16:00:01", "s", {"SPEED": 0}),
                ("16:00:02", "s", {"SPEED": 0}),
                ("16:00:03", "s", {"SPEED": 7}),
            ]
        )
        render.print_compact(results, ["SPEED"], {"SPEED": {"unit": ""}}, set(), changes_only=True)
        out = capsys.readouterr().out
        # First 0 prints, next two identical 0s are dropped, then 7 prints.
        assert "16:00:00" in out
        assert "16:00:01" not in out
        assert "16:00:02" not in out
        assert "16:00:03" in out

    def test_no_present_params(self, capsys):
        results = _compact_results([("16:00:00", "s", {})])
        render.print_compact(results, ["SPEED"], {"SPEED": {"unit": ""}}, set())
        out = capsys.readouterr().out
        assert "no decodable parameters" in out


# ---------------------------------------------------------------------------
# --group-by state stats
# ---------------------------------------------------------------------------


class TestPrintStatsGrouped:
    def test_groups_by_state(self, capsys):
        results = _compact_results(
            [
                ("16:00:00", "drive A", {"P": 1}),
                ("16:00:01", "drive A", {"P": 3}),
                ("16:00:02", "drive B", {"P": 100}),
            ]
        )
        render.print_stats_grouped(results, ["P"], {"P": {"unit": ""}}, set(), "state")
        out = capsys.readouterr().out
        assert "[drive A]" in out and "[drive B]" in out
        # Group A max is 3, group B max is 100 — proves per-group stats.
        assert "max=3" in out
        assert "max=100" in out


# ---------------------------------------------------------------------------
# captures/decode share the one scoping surface (regression guard)
# ---------------------------------------------------------------------------


class TestSharedScopingWiring:
    def test_captures_uses_shared_helpers(self):
        # captures must import the shared module (not redefine local date helpers),
        # so both commands stay in lockstep and expose --state/--label.
        from canlib import capture_dates
        from canlib.commands.captures import uds as cap

        assert cap.filter_by_date_range is capture_dates.filter_by_date_range
        assert cap.filter_by_text is capture_dates.filter_by_text
        assert cap.resolve_scope_bounds is capture_dates.resolve_scope_bounds
        assert cap.add_scope_args is capture_dates.add_scope_args

    def test_both_commands_register_scope_flags(self):
        import argparse

        from canlib.commands import captures as cap
        from canlib.commands import decode as dec

        # decode is flat; captures is a uds/can group — its scope flags live on
        # the uds kind (parsed via a resolved sub-namespace).
        sub = argparse.ArgumentParser().add_subparsers()
        dec_parser = dec.add_parser(sub)
        dec_opts = {a for action in dec_parser._actions for a in action.option_strings}
        assert {
            "--since",
            "--until",
            "--date",
            "--state",
            "--label",
            "--today",
            "--last-sessions",
            "--last-session",
        } <= dec_opts, dec.NAME

        sub2 = argparse.ArgumentParser().add_subparsers()
        cap_group = cap.add_parser(sub2)
        cap_uds = cap_group.parse_args(["uds", "BMS"])
        for flag in ("since", "until", "date", "state", "label"):
            assert hasattr(cap_uds, flag), f"captures uds missing --{flag}"

    def test_decode_has_analysis_modifiers(self):
        import argparse

        from canlib.commands import decode as dec

        sub = argparse.ArgumentParser().add_subparsers()
        parser = dec.add_parser(sub)
        opts = {a for action in parser._actions for a in action.option_strings}
        assert {"--changes-only", "--group-by", "--first", "--last"} <= opts
