"""Tests for `canair ecu` list/detail — CAN bus display, sorting, no IDENT column."""

from __future__ import annotations

import pytest

from canlib import profile
from canlib.commands.ecu import _detail_record, _list_records, cmd_list
from canlib.pids import clear_cache


@pytest.fixture(autouse=True)
def _restore_active_profile():
    from canlib import config

    saved = profile._active
    clear_cache()
    config.load_config.cache_clear()
    yield
    profile._active = saved
    clear_cache()
    config.load_config.cache_clear()


def _ecus():
    return {
        0x770: {"name": "GW", "can_bus": ["All"]},
        0x7E3: {"name": "MCU", "can_bus": ["H", "P"]},
        0x7A0: {"name": "BCM", "can_bus": ["B"]},
        0x7D2: {"name": "SRS"},  # no bus
    }


def test_records_carry_can_bus():
    recs = _list_records(_ecus(), {"ecus": {}})
    by_name = {r["name"]: r for r in recs}
    assert by_name["MCU"]["can_bus"] == ["H", "P"]
    assert by_name["SRS"]["can_bus"] is None


def test_sort_by_bus_groups_and_puts_unbussed_last():
    recs = _list_records(_ecus(), {"ecus": {}}, sort="bus")
    order = [r["name"] for r in recs]
    # All < B < H/P, unbussed (SRS) last.
    assert order == ["GW", "BCM", "MCU", "SRS"]


def test_sort_by_name_default():
    recs = _list_records(_ecus(), {"ecus": {}}, sort="name")
    assert [r["name"] for r in recs] == ["BCM", "GW", "MCU", "SRS"]


def test_no_identity_confidence_in_records():
    recs = _list_records(_ecus(), {"ecus": {}})
    assert all("identity_confidence" not in r for r in recs)


def test_detail_record_resolves_bus_labels():
    info = {"name": "MCU", "can_bus": ["H", "P"]}
    labels = {"H": "Hybrid CAN", "P": "Powertrain CAN"}
    rec = _detail_record(info, 0x7E3, None, None, bus_labels=labels)
    assert rec["can_bus_labels"] == ["Hybrid CAN", "Powertrain CAN"]
    assert "identity_confidence" not in rec


def test_list_output_has_no_ident_column(capsys):
    recs = _list_records(_ecus(), {"ecus": {}})
    cmd_list(recs, as_json=False)
    out = capsys.readouterr().out
    assert "IDENT" not in out
    assert "BUS" in out
