"""Tests for canlib.dtc_log — DTC scan history (append + diff)."""

from canlib import dtc_log


def test_append_and_load(tmp_path):
    p = tmp_path / "dtc_log.yaml"
    entry = dtc_log.build_scan(
        "all",
        {"AMP (0x783)": {"tx": "0x783", "protocol": "kwp", "dtcs": ["B2915-00"]}},
        label="baseline",
        timestamp="2026-07-22T10:00:00",
    )
    dtc_log.append_scan(entry, path=p)
    data = dtc_log.load_log(p)
    assert len(data["scans"]) == 1
    assert data["scans"][0]["scope"] == "all"
    assert data["scans"][0]["label"] == "baseline"


def test_load_missing_is_empty(tmp_path):
    assert dtc_log.load_log(tmp_path / "nope.yaml") == {"scans": []}


def test_latest_matching_by_scope(tmp_path):
    p = tmp_path / "dtc_log.yaml"
    dtc_log.append_scan(dtc_log.build_scan("all", {}, timestamp="t1"), path=p)
    dtc_log.append_scan(dtc_log.build_scan("BMS (0x7E4)", {}, timestamp="t2"), path=p)
    dtc_log.append_scan(dtc_log.build_scan("all", {}, timestamp="t3"), path=p)
    assert dtc_log.latest_matching("all", path=p)["timestamp"] == "t3"
    assert dtc_log.latest_matching("BMS (0x7E4)", path=p)["timestamp"] == "t2"
    assert dtc_log.latest_matching("VCU (0x7E2)", path=p) is None


def test_diff_scans():
    prev = {
        "ecus": {
            "AMP (0x783)": {"dtcs": ["B2915-00", "B2916-00"]},
            "PLC (0x733)": {"dtcs": ["C182C-00"]},
        }
    }
    curr = {"ecus": {"AMP (0x783)": {"dtcs": ["B2915-00", "B9999-00"]}}}
    d = dtc_log.diff_scans(prev, curr)
    # PLC gone entirely + AMP lost one code -> both cleared
    assert ("AMP (0x783)", "B2916-00") in d["cleared"]
    assert ("PLC (0x733)", "C182C-00") in d["cleared"]
    assert ("AMP (0x783)", "B9999-00") in d["new"]
    assert ("AMP (0x783)", "B2915-00") in d["persisting"]


def test_diff_no_change():
    scan = {"ecus": {"AMP (0x783)": {"dtcs": ["B2915-00"]}}}
    d = dtc_log.diff_scans(scan, scan)
    assert d["cleared"] == [] and d["new"] == []
    assert d["persisting"] == [("AMP (0x783)", "B2915-00")]


def test_format_diff_reports_cleared():
    prev = {"timestamp": "t0", "ecus": {"AMP (0x783)": {"dtcs": ["B2915-00"]}}}
    curr = {"ecus": {}}
    lines = "\n".join(dtc_log.format_diff(dtc_log.diff_scans(prev, curr), prev))
    assert "cleared" in lines
    assert "B2915-00" in lines


def test_append_clear_and_coexist_with_scans(tmp_path):
    p = tmp_path / "dtc_log.yaml"
    dtc_log.append_scan(dtc_log.build_scan("all", {}, timestamp="t1"), path=p)
    dtc_log.append_clear(
        dtc_log.build_clear(
            "manual", "BMS (0x7E4)", ecu="BMS (0x7E4)", protocol="kwp",
            group="0xFFFF", codes=["P1AAA-00"], timestamp="t2",
        ),
        path=p,
    )
    data = dtc_log.load_log(p)
    assert len(data["scans"]) == 1
    assert len(data["clears"]) == 1
    clr = data["clears"][0]
    assert clr["type"] == "manual"
    assert clr["ecu"] == "BMS (0x7E4)"
    assert clr["codes"] == ["P1AAA-00"]
    assert clr["timestamp"] == "t2"


def test_build_clear_omits_none_fields():
    e = dtc_log.build_clear("detected", "all", cleared=[["PLC (0x733)", "C182C-00"]], codes=None)
    assert "codes" not in e
    assert e["type"] == "detected"
    assert e["cleared"] == [["PLC (0x733)", "C182C-00"]]
