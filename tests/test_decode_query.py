"""Tests for decode's mini-language QUERY resolution (canair decode BMS:2101 …)."""

from __future__ import annotations

from canlib.commands.captures import build_query
from canlib.commands.decode import _resolve_targets


def _index():
    return {
        "BMS": {"pids": {"2101": {}, "2102": {}, "22BC03": {}}},
        "MCU": {"pids": {"2101": {}, "2102": {}}},
    }


def test_two_token_form_collapses_to_colon():
    # `decode BMS 2101` → same as `decode BMS:2101`.
    assert build_query(["BMS", "2101"]) == "BMS:2101"


def test_single_pid():
    targets, err = _resolve_targets("BMS:2101", _index(), tolerate_missing=False)
    assert err is None
    assert targets == [("BMS", "2101")]


def test_all_pids_for_ecu():
    targets, err = _resolve_targets("BMS", _index(), tolerate_missing=False)
    assert err is None
    assert targets == [("BMS", "2101"), ("BMS", "2102"), ("BMS", "22BC03")]


def test_prefix_pid_match():
    targets, err = _resolve_targets("BMS:22", _index(), tolerate_missing=False)
    assert err is None
    assert targets == [("BMS", "22BC03")]


def test_suffix_pid_match():
    # Short form "BC03" resolves to the defined full DID "22BC03".
    targets, err = _resolve_targets("BMS:BC03", _index(), tolerate_missing=False)
    assert err is None
    assert targets == [("BMS", "22BC03")]


def test_cross_ecu_selectors():
    targets, err = _resolve_targets("BMS:2101 MCU:2102", _index(), tolerate_missing=False)
    assert err is None
    assert targets == [("BMS", "2101"), ("MCU", "2102")]


def test_dedupes_overlapping_selectors():
    targets, err = _resolve_targets("BMS:2101 BMS", _index(), tolerate_missing=False)
    assert err is None
    # 2101 appears once despite matching both selectors.
    assert targets == [("BMS", "2101"), ("BMS", "2102"), ("BMS", "22BC03")]


def test_undefined_single_pid_kept_as_literal():
    # An explicit unknown PID is kept so _decode_one can print not-found / --try.
    targets, err = _resolve_targets("BMS:9999", _index(), tolerate_missing=True)
    assert err is None
    assert targets == [("BMS", "9999")]


def test_unknown_ecu_errors():
    targets, err = _resolve_targets("NOPE", _index(), tolerate_missing=False)
    assert targets == []
    assert err is not None
    assert "Available ECUs" in err


def test_comma_pid_list():
    targets, err = _resolve_targets("BMS:2101,2102", _index(), tolerate_missing=False)
    assert err is None
    assert targets == [("BMS", "2101"), ("BMS", "2102")]
