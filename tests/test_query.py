"""Tests for the ECU/PID query mini-language (canlib.query)."""

import pytest

from canlib.query import (
    QueryError,
    Selector,
    parse_query,
    parse_selector,
)


class TestParseSelector:
    def test_ecu_only(self):
        s = parse_selector("VCU")
        assert s == Selector("VCU", ())

    def test_ecu_case_insensitive(self):
        assert parse_selector("vcu").ecu == "VCU"

    def test_single_pid(self):
        assert parse_selector("VCU:2101") == Selector("VCU", ("2101",))

    def test_pid_upper(self):
        assert parse_selector("igpm:22bc03") == Selector("IGPM", ("22BC03",))

    def test_pid_list(self):
        assert parse_selector("VCU:2101,22BC03") == Selector("VCU", ("2101", "22BC03"))

    def test_pid_list_whitespace_and_empties(self):
        # empty tokens are dropped
        assert parse_selector("VCU:2101, ,2102,") == Selector("VCU", ("2101", "2102"))

    def test_trailing_colon_means_all(self):
        assert parse_selector("VCU:") == Selector("VCU", ())

    def test_empty_ecu_raises(self):
        with pytest.raises(QueryError):
            parse_selector(":2101")

    def test_double_colon_raises(self):
        with pytest.raises(QueryError):
            parse_selector("VCU:2101:extra")


class TestParseQuery:
    def test_single_selector(self):
        q = parse_query("VCU")
        assert q.selectors == (Selector("VCU", ()),)

    def test_multiple_selectors(self):
        q = parse_query("VCU:2101 BMS:2101,2102")
        assert q.selectors == (
            Selector("VCU", ("2101",)),
            Selector("BMS", ("2101", "2102")),
        )

    def test_extra_whitespace(self):
        q = parse_query("  VCU   BMS  ")
        assert q.selectors == (Selector("VCU", ()), Selector("BMS", ()))

    def test_token_list_input(self):
        # argparse nargs="+" hands us a list
        q = parse_query(["VCU:2101", "BMS"])
        assert q.selectors == (Selector("VCU", ("2101",)), Selector("BMS", ()))

    def test_empty_raises(self):
        with pytest.raises(QueryError):
            parse_query("")
        with pytest.raises(QueryError):
            parse_query("   ")


class TestCanonicalizeEcus:
    def test_maps_alias_to_primary(self):
        q = parse_query("SMK:2101 BMS")
        resolver = {"SMK": "SKM"}
        out = q.canonicalize_ecus(lambda e: resolver.get(e, e))
        assert out.selectors == (
            Selector("SKM", ("2101",)),
            Selector("BMS", ()),
        )

    def test_preserves_pids_and_unknown_ecus(self):
        q = parse_query("FOO:2101,2102")
        out = q.canonicalize_ecus(lambda e: e)  # identity resolver
        assert out.selectors == (Selector("FOO", ("2101", "2102")),)


class TestSelectorMatching:
    def test_ecu_only_matches_all_pids(self):
        s = Selector("VCU", ())
        assert s.matches("VCU", "2101")
        assert s.matches("vcu", "ANYTHING")
        assert not s.matches("BMS", "2101")

    def test_exact_pid(self):
        s = Selector("VCU", ("2101",))
        assert s.matches("VCU", "2101")
        assert not s.matches("VCU", "2102")

    def test_substring_pid(self):
        s = Selector("BCM", ("22",))
        assert s.matches("BCM", "22BC03")
        assert s.matches("BCM", "22C00B")
        assert not s.matches("BCM", "2101")

    def test_pid_list_or(self):
        s = Selector("VCU", ("2101", "2102"))
        assert s.matches("VCU", "2101")
        assert s.matches("VCU", "2102")
        assert not s.matches("VCU", "21F2")

    def test_pid_int_coerced(self):
        s = Selector("VCU", ("2101",))
        assert s.matches("VCU", 2101)  # non-string pid


class TestQueryFilter:
    def _records(self):
        return [
            {"ecu": "VCU", "pid": "2101", "x": 1},
            {"ecu": "VCU", "pid": "2102", "x": 2},
            {"ecu": "VCU", "pid": "21F2", "x": 3},
            {"ecu": "BMS", "pid": "2101", "x": 4},
            {"ecu": "IGPM", "pid": "22BC03", "x": 5},
        ]

    def _filter(self, query):
        return parse_query(query).filter(
            self._records(), ecu_of=lambda r: r["ecu"], pid_of=lambda r: r["pid"]
        )

    def test_all_pids_for_ecu(self):
        matched, empty = self._filter("VCU")
        assert [r["x"] for r in matched] == [1, 2, 3]
        assert empty == []

    def test_single_pid(self):
        matched, _empty = self._filter("VCU:2101")
        assert [r["x"] for r in matched] == [1]

    def test_multi_pid(self):
        matched, _ = self._filter("VCU:2101,2102")
        assert [r["x"] for r in matched] == [1, 2]

    def test_cross_ecu(self):
        matched, empty = self._filter("VCU:2101 BMS:2101")
        assert [r["x"] for r in matched] == [1, 4]
        assert empty == []

    def test_substring(self):
        matched, _ = self._filter("IGPM:22")
        assert [r["x"] for r in matched] == [5]

    def test_preserves_input_order(self):
        matched, _ = self._filter("BMS:2101 VCU:2101")
        # order follows records, not selector order
        assert [r["x"] for r in matched] == [1, 4]

    def test_empty_selectors_reported(self):
        matched, empty = self._filter("VCU:2101 NOPE:9999")
        assert [r["x"] for r in matched] == [1]
        assert empty == [Selector("NOPE", ("9999",))]

    def test_record_matched_once_even_if_two_selectors_hit(self):
        # VCU:2101 matched by both selectors; record appears once
        matched, empty = self._filter("VCU VCU:2101")
        assert [r["x"] for r in matched] == [1, 2, 3]
        assert empty == []


class TestStr:
    def test_selector_str_roundtrip(self):
        assert str(Selector("VCU", ())) == "VCU"
        assert str(Selector("VCU", ("2101", "2102"))) == "VCU:2101,2102"

    def test_query_str(self):
        assert str(parse_query("vcu:2101 bms")) == "VCU:2101 BMS"
