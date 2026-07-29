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
        0x770: {"name": "GW", "can_bus": ["ALL"]},
        0x7E3: {"name": "MCU", "can_bus": ["H-CAN", "P-CAN"]},
        0x7A0: {"name": "BCM", "can_bus": ["B-CAN"]},
        0x7D2: {"name": "SRS"},  # no bus
    }


def test_records_carry_can_bus():
    recs = _list_records(_ecus(), {"ecus": {}})
    by_name = {r["name"]: r for r in recs}
    assert by_name["MCU"]["can_bus"] == ["H-CAN", "P-CAN"]
    assert by_name["SRS"]["can_bus"] is None


def test_sort_by_bus_groups_and_puts_unbussed_last():
    recs = _list_records(_ecus(), {"ecus": {}}, sort="bus")
    order = [r["name"] for r in recs]
    # ALL < B-CAN < H-CAN/P-CAN, unbussed (SRS) last.
    assert order == ["GW", "BCM", "MCU", "SRS"]


def test_sort_by_name_default():
    recs = _list_records(_ecus(), {"ecus": {}}, sort="name")
    assert [r["name"] for r in recs] == ["BCM", "GW", "MCU", "SRS"]


def test_sort_by_bus_is_default():
    # No explicit sort → bus grouping (unbussed last).
    recs = _list_records(_ecus(), {"ecus": {}})
    assert [r["name"] for r in recs] == ["GW", "BCM", "MCU", "SRS"]


def test_sort_by_tx_ascending():
    recs = _list_records(_ecus(), {"ecus": {}}, sort="tx")
    # Numeric/hex ascending: 0x770 < 0x7A0 < 0x7D2 < 0x7E3.
    assert [r["tx"] for r in recs] == ["0x770", "0x7A0", "0x7D2", "0x7E3"]


def test_sort_by_proto_ascending_missing_last():
    ecus = {
        0x1: {"name": "A", "id_protocol": "UDS"},
        0x2: {"name": "B", "id_protocol": "KWP2000"},
        0x3: {"name": "C"},  # no protocol → last
    }
    recs = _list_records(ecus, {"ecus": {}}, sort="proto")
    assert [r["name"] for r in recs] == ["B", "A", "C"]


def _pids_data_counts():
    return {
        "ecus": {
            "A": {"tx_id": 0x1, "pids": {"2101": {"parameters": {}}}},
            "B": {
                "tx_id": 0x2,
                "pids": {
                    "2101": {"parameters": {"x": {"verified": True}}},
                    "2102": {"parameters": {"y": {}}},
                    "2103": {"parameters": {}},
                },
            },
        }
    }


def test_sort_by_pids_descending():
    ecus = {0x1: {"name": "A"}, 0x2: {"name": "B"}, 0x3: {"name": "C"}}
    recs = _list_records(ecus, _pids_data_counts(), sort="pids")
    # B (3 pids) > A (1 pid) > C (registry-only, no pids) last.
    assert [r["name"] for r in recs] == ["B", "A", "C"]


def test_sort_by_verif_descending():
    ecus = {0x1: {"name": "A"}, 0x2: {"name": "B"}, 0x3: {"name": "C"}}
    recs = _list_records(ecus, _pids_data_counts(), sort="verif")
    # B has 1 verified, A has 0, C has none (registry-only) → last.
    assert [r["name"] for r in recs] == ["B", "A", "C"]


def test_sort_by_caps_descending_none_last(monkeypatch):
    from collections import Counter

    import canlib.commands.ecu as ecu

    monkeypatch.setattr(ecu, "_all_captures_by_ecu", lambda: Counter({"A": 5, "B": 9}))
    ecus = {0x1: {"name": "A"}, 0x2: {"name": "B"}, 0x3: {"name": "C"}}
    recs = _list_records(ecus, _pids_data_counts(), with_captures=True, sort="caps")
    # B (9) > A (5) > C (registry-only, no captures key) last.
    assert [r["name"] for r in recs] == ["B", "A", "C"]


def test_no_identity_confidence_in_records():
    recs = _list_records(_ecus(), {"ecus": {}})
    assert all("identity_confidence" not in r for r in recs)


def test_detail_record_resolves_bus_labels():
    info = {"name": "MCU", "can_bus": ["H-CAN", "P-CAN"]}
    labels = {"H-CAN": "Hybrid CAN", "P-CAN": "Powertrain CAN"}
    rec = _detail_record(info, 0x7E3, None, None, bus_labels=labels)
    assert rec["can_bus_labels"] == ["Hybrid CAN", "Powertrain CAN"]
    assert "identity_confidence" not in rec


def test_list_output_has_no_ident_column(capsys):
    recs = _list_records(_ecus(), {"ecus": {}})
    cmd_list(recs, as_json=False)
    out = capsys.readouterr().out
    assert "IDENT" not in out
    assert "BUS" in out


def test_list_output_omits_alias_suffix(capsys):
    ecus = {0x7A5: {"name": "SKM", "alias": "SMK", "can_bus": ["B-CAN"]}}
    recs = _list_records(ecus, {"ecus": {}})
    cmd_list(recs, as_json=False)
    out = capsys.readouterr().out
    assert "SKM" in out
    assert "SMK" not in out


# ── capture-count opt-in (--captures) ────────────────────────────────────────
# Capture counts require parsing every capture file, so they are opt-in. Without
# --captures the counts must be None ("not computed", rendered "—"), never 0.


def _pids_data_bms():
    return {
        "ecus": {
            "BMS": {
                "tx_id": 0x7E4,
                "pids": {"2101": {"parameters": {"SOC": {"verified": True}}}},
            }
        }
    }


def test_list_captures_none_without_flag(monkeypatch):
    # _all_captures_by_ecu must NOT be called when captures aren't requested.
    import canlib.commands.ecu as ecu

    def _boom():
        raise AssertionError("_all_captures_by_ecu called without --captures")

    monkeypatch.setattr(ecu, "_all_captures_by_ecu", _boom)
    recs = _list_records({0x7E4: {"name": "BMS"}}, _pids_data_bms(), with_captures=False)
    assert recs[0]["captures"] is None


def test_list_captures_counted_with_flag(monkeypatch):
    from collections import Counter

    import canlib.commands.ecu as ecu

    monkeypatch.setattr(ecu, "_all_captures_by_ecu", lambda: Counter({"BMS": 42}))
    recs = _list_records({0x7E4: {"name": "BMS"}}, _pids_data_bms(), with_captures=True)
    assert recs[0]["captures"] == 42


def test_list_caps_column_shows_dash_without_flag(capsys):
    recs = _list_records({0x7E4: {"name": "BMS"}}, _pids_data_bms(), with_captures=False)
    cmd_list(recs, as_json=False)
    out = capsys.readouterr().out
    assert "—" in out  # CAPS rendered as em-dash, not "0"


def test_detail_captures_none_without_flag(monkeypatch):
    import canlib.commands.ecu as ecu

    def _boom(_name):
        raise AssertionError("_captures_by_pid called without --captures")

    monkeypatch.setattr(ecu, "_captures_by_pid", _boom)
    info = {"name": "BMS"}
    ecu_def = _pids_data_bms()["ecus"]["BMS"]
    rec = _detail_record(info, 0x7E4, "BMS", ecu_def, with_captures=False)
    assert rec["captures"] is None
    assert rec["pid_list"][0]["captures"] is None


def test_detail_captures_counted_with_flag(monkeypatch):
    from collections import Counter

    import canlib.commands.ecu as ecu

    monkeypatch.setattr(ecu, "_captures_by_pid", lambda _name: (Counter({"2101": 7}), 7))
    info = {"name": "BMS"}
    ecu_def = _pids_data_bms()["ecus"]["BMS"]
    rec = _detail_record(info, 0x7E4, "BMS", ecu_def, with_captures=True)
    assert rec["captures"] == 7
    assert rec["pid_list"][0]["captures"] == 7


def test_detail_display_omits_captures_without_flag(capsys):
    from canlib.commands.ecu import cmd_detail

    info = {"name": "BMS"}
    ecu_def = _pids_data_bms()["ecus"]["BMS"]
    rec = _detail_record(info, 0x7E4, "BMS", ecu_def, with_captures=False)
    cmd_detail(rec, as_json=False)
    out = capsys.readouterr().out
    assert "Captures" not in out
    assert "cap" not in out  # no "N cap" per-PID segment


# ── pids view (ecu <name> pids) ──────────────────────────────────────────────


def _pids_data_multi():
    return {
        "tx_id": 0x7E4,
        "pids": {
            "2101": {"parameters": {"SOC": {"expression": "B1"}}},
            "2102": {"status": "draft", "parameters": {"CELL": {"expression": "B1"}}},
            "2180": {"parameters": {}},  # no params defined
        },
    }


def test_wrap_pairs_wraps_to_width():
    from canlib.commands.ecu import _wrap_pairs

    pairs = ["A=1", "B=2", "C=3"]
    # width just enough for one pair + indent → one pair per line
    lines = _wrap_pairs(pairs, width=len("  A=1"), indent="  ")
    assert lines == ["  A=1", "  B=2", "  C=3"]


def test_pids_latest_records_shape(monkeypatch):
    import canlib.commands.ecu as ecu

    # Latest capture only for 2101; 2102/2180 have none.
    monkeypatch.setattr(
        ecu,
        "_latest_capture_by_pid",
        lambda name: {
            "2101": {
                "payload": "620...",
                "date": "2026-07-22",
                "time": "10:00",
                "vehicle_states": ["ready"],
            }
        },
    )
    monkeypatch.setattr(
        "canlib.commands._captures_query._decoded_preview", lambda e: {"SOC": "79.0 %"}
    )
    recs = ecu._pids_latest_records(_pids_data_multi(), "BMS")
    by_pid = {r["pid"]: r for r in recs}
    assert by_pid["2101"]["values"] == {"SOC": "79.0 %"}
    assert by_pid["2101"]["vehicle_states"] == ["ready"]
    assert by_pid["2102"]["values"] is None
    assert by_pid["2102"]["status"] == "draft"
    assert by_pid["2180"]["n_params"] == 0


def test_cmd_pids_renders_values_not_hex(monkeypatch, capsys):
    import canlib.commands.ecu as ecu

    monkeypatch.setattr(
        ecu,
        "_latest_capture_by_pid",
        lambda name: {"2101": {"payload": "6201DEADBEEF", "date": "2026-07-22"}},
    )
    monkeypatch.setattr(
        "canlib.commands._captures_query._decoded_preview", lambda e: {"SOC": "79.0 %"}
    )
    rc = ecu.cmd_pids({"name": "BMS"}, 0x7E4, _pids_data_multi(), as_json=False)
    out = capsys.readouterr().out
    assert rc == 0
    assert "SOC=79.0 %" in out
    assert "DEADBEEF" not in out  # never shows raw hex
    assert "no capture" in out  # 2102 has params but no capture
    assert "no parameters defined" in out  # 2180
    assert "canair captures BMS" in out  # pointer to full history


def test_cmd_pids_json(monkeypatch, capsys):
    import json

    import canlib.commands.ecu as ecu

    monkeypatch.setattr(ecu, "_latest_capture_by_pid", lambda name: {})
    rc = ecu.cmd_pids({"name": "BMS"}, 0x7E4, _pids_data_multi(), as_json=True)
    out = capsys.readouterr().out
    assert rc == 0
    data = json.loads(out)
    assert data["ecu"] == "BMS"
    assert {p["pid"] for p in data["pids"]} == {"2101", "2102", "2180"}


def test_cmd_pids_identity_only(capsys):
    import canlib.commands.ecu as ecu

    rc = ecu.cmd_pids({"name": "SRS"}, 0x7D2, None, as_json=False)
    out = capsys.readouterr().out
    assert rc == 0
    assert "No PID definitions" in out
