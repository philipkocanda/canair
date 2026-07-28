"""Tests for the transport diagnostics recorder + response classification."""

from __future__ import annotations

from canlib.transport_stats import TransportStats, classify_raw_value
from canlib.uds_parse import (
    ERROR_CATEGORIES,
    RESPONSE_CATEGORIES,
    classify_error,
    classify_response,
    parse_uds_response,
)


class TestClassifyResponse:
    def test_positive_is_ok(self):
        assert classify_response(parse_uds_response("62 F1 90 01")) == "ok"

    def test_negative_is_nrc(self):
        # 7F 22 31 = requestOutOfRange for service 0x22 — a real ECU answer.
        assert classify_response(parse_uds_response("7F 22 31")) == "nrc"

    def test_truncated_multiframe_is_drop(self):
        # ELM327-style multi-frame: declared 27 bytes but only ~14 delivered.
        raw = "22C00B\n017\n0:62C00BFFFF00\n1:00C84F0100C84E"
        resp = parse_uds_response(raw)
        assert resp.get("error", "").startswith("truncated ISO-TP")
        assert classify_response(resp) == "drop"

    def test_noncontiguous_multiframe_is_drop(self):
        # A gap in the frame counter (0 then 2) — a dropped consecutive frame.
        raw = "22C00B\n017\n0:62C00BFFFF00\n2:00C84F0100C84E"
        resp = parse_uds_response(raw)
        assert classify_response(resp) == "drop"

    def test_sid_mismatch_is_stale(self):
        resp = parse_uds_response("62 C0 0B", expected_sid=0x21)
        assert classify_response(resp) == "stale"

    def test_no_data_is_no_data(self):
        assert classify_response(parse_uds_response("NO DATA")) == "no_data"

    def test_can_error_is_bus(self):
        assert classify_response(parse_uds_response("CAN ERROR")) == "bus"

    def test_nonhex_is_decode(self):
        assert classify_response(parse_uds_response("62 ZZ 90")) == "decode"


class TestClassifyError:
    def test_unknown_error_is_other(self):
        assert classify_error("something we never emit") == "other"

    def test_empty_is_other(self):
        assert classify_error(None) == "other"
        assert classify_error("") == "other"

    def test_every_category_is_a_valid_bucket(self):
        for cat in ERROR_CATEGORIES:
            assert cat in RESPONSE_CATEGORIES


class TestClassifyRawValue:
    def test_none_is_no_data(self):
        assert classify_raw_value(None) == "no_data"

    def test_timeout_exception_is_no_data(self):
        assert classify_raw_value(TimeoutError("no response")) == "no_data"

    def test_other_exception_is_bus(self):
        assert classify_raw_value(RuntimeError("bus down")) == "bus"

    def test_positive_bytes_is_ok(self):
        assert classify_raw_value(bytes.fromhex("62F19001")) == "ok"

    def test_negative_bytes_is_nrc(self):
        assert classify_raw_value(bytes.fromhex("7F2231")) == "nrc"

    def test_empty_bytes_is_no_data(self):
        assert classify_raw_value(b"") == "no_data"


class TestTransportStats:
    def test_counts_and_totals(self):
        d = TransportStats(transport="slcan-tcp")
        d.record("ok")
        d.record("ok")
        d.record("drop")
        d.record("no_data")
        d.record("nrc")
        assert d.exchanges == 5
        assert d.counts["ok"] == 2
        assert d.drops == 1  # drop + stale
        assert d.errors == 2  # drop + no_data (nrc excluded)

    def test_unknown_category_falls_into_other(self):
        d = TransportStats()
        d.record("bogus")
        assert d.counts["other"] == 1

    def test_stale_counts_toward_drops(self):
        d = TransportStats()
        d.record("stale")
        assert d.drops == 1
        assert d.errors == 1

    def test_quality_omits_zero_error_categories(self):
        d = TransportStats()
        for _ in range(3):
            d.record("ok")
        d.record("drop")
        q = d.quality()
        assert q["exchanges"] == 4
        assert q["drop"] == 1
        # Clean categories and nrc are not present.
        assert "no_data" not in q
        assert "nrc" not in q

    def test_quality_clean_session_is_just_exchanges(self):
        d = TransportStats()
        d.record("ok")
        d.record("nrc")
        assert d.quality() == {"exchanges": 2}

    def test_diff_is_delta_since_base(self):
        d = TransportStats()
        d.record("ok")
        base = d.snapshot()
        d.record("ok")
        d.record("drop")
        delta = d.diff(base)
        assert delta.exchanges == 2
        assert delta.drops == 1
        assert delta.counts["ok"] == 1

    def test_record_response_classifies(self):
        d = TransportStats()
        cat = d.record_response(parse_uds_response("NO DATA"), pid="2101")
        assert cat == "no_data"
        assert d.counts["no_data"] == 1

    def test_record_raw_classifies(self):
        d = TransportStats()
        assert d.record_raw(None, pid="2101") == "no_data"
        assert d.record_raw(bytes.fromhex("62F190"), pid="F190") == "ok"
        assert d.counts["no_data"] == 1
        assert d.counts["ok"] == 1

    def test_bool_reflects_activity(self):
        d = TransportStats()
        assert not d
        d.record("ok")
        assert d
