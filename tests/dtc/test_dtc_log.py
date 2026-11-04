"""Tests for canlib.dtc.log — DTC scan history (append + diff)."""

from canlib.dtc import log as dtc_log


def test_append_and_load(tmp_path):
    p = tmp_path / "dtc_log.yaml"
    entry = dtc_log.build_scan(
        "all",
        {"0x78B": {"tx": "0x783", "protocol": "kwp", "dtcs": ["B2915-00"]}},
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


def test_build_scan_records_vehicle_states(tmp_path):
    p = tmp_path / "dtc_log.yaml"
    entry = dtc_log.build_scan(
        "all",
        {},
        label="on-charge",
        vehicle_states=["plugged", "charging"],
        timestamp="2026-07-22T10:00:00",
    )
    assert entry["vehicle_states"] == ["plugged", "charging"]
    dtc_log.append_scan(entry, path=p)
    assert dtc_log.load_log(p)["scans"][0]["vehicle_states"] == ["plugged", "charging"]


def test_build_scan_omits_empty_vehicle_states():
    # No states (None or []) → the field is left off the entry entirely.
    assert "vehicle_states" not in dtc_log.build_scan("all", {}, timestamp="t")
    assert "vehicle_states" not in dtc_log.build_scan("all", {}, vehicle_states=[], timestamp="t")


def test_latest_matching_by_scope(tmp_path):
    p = tmp_path / "dtc_log.yaml"
    dtc_log.append_scan(dtc_log.build_scan("all", {}, timestamp="t1"), path=p)
    dtc_log.append_scan(dtc_log.build_scan("0x7EC", {}, timestamp="t2"), path=p)
    dtc_log.append_scan(dtc_log.build_scan("all", {}, timestamp="t3"), path=p)
    assert dtc_log.latest_matching("all", path=p)["timestamp"] == "t3"
    assert dtc_log.latest_matching("0x7EC", path=p)["timestamp"] == "t2"
    assert dtc_log.latest_matching("0x7EA", path=p) is None


def test_diff_scans():
    prev = {
        "ecus": {
            "0x78B": {"dtcs": ["B2915-00", "B2916-00"]},
            "0x73B": {"dtcs": ["C182C-00"]},
        }
    }
    curr = {"ecus": {"0x78B": {"dtcs": ["B2915-00", "B9999-00"]}}}
    d = dtc_log.diff_scans(prev, curr)
    # PLC gone entirely + AMP lost one code -> both cleared
    assert ("0x78B", "B2916-00") in d["cleared"]
    assert ("0x73B", "C182C-00") in d["cleared"]
    assert ("0x78B", "B9999-00") in d["new"]
    assert ("0x78B", "B2915-00") in d["persisting"]


def test_diff_no_change():
    scan = {"ecus": {"0x78B": {"dtcs": ["B2915-00"]}}}
    d = dtc_log.diff_scans(scan, scan)
    assert d["cleared"] == [] and d["new"] == []
    assert d["persisting"] == [("0x78B", "B2915-00")]


def test_format_diff_reports_cleared():
    prev = {"timestamp": "t0", "ecus": {"0x78B": {"dtcs": ["B2915-00"]}}}
    curr = {"ecus": {}}
    lines = "\n".join(dtc_log.format_diff(dtc_log.diff_scans(prev, curr), prev))
    assert "cleared" in lines
    assert "B2915-00" in lines


def test_append_clear_and_coexist_with_scans(tmp_path):
    p = tmp_path / "dtc_log.yaml"
    dtc_log.append_scan(dtc_log.build_scan("all", {}, timestamp="t1"), path=p)
    dtc_log.append_clear(
        dtc_log.build_clear(
            "0x7EC", protocol="kwp", group="0xFFFF", codes=["P1AAA-00"], timestamp="t2"
        ),
        path=p,
    )
    data = dtc_log.load_log(p)
    assert len(data["scans"]) == 1
    assert len(data["clears"]) == 1
    clr = data["clears"][0]
    assert clr["rx"] == "0x7EC"
    assert clr["codes"] == ["P1AAA-00"]
    assert clr["timestamp"] == "t2"


def test_build_clear_omits_none_fields():
    e = dtc_log.build_clear("0x73B", codes=None, timestamp="t")
    assert e == {"timestamp": "t", "rx": "0x73B"}


def test_prior_matching_returns_scan_before_timestamp(tmp_path):
    p = tmp_path / "dtc_log.yaml"
    dtc_log.append_scan(dtc_log.build_scan("all", {}, timestamp="t1"), path=p)
    dtc_log.append_scan(dtc_log.build_scan("0x7EC", {}, timestamp="t2"), path=p)
    dtc_log.append_scan(dtc_log.build_scan("all", {}, timestamp="t3"), path=p)
    # The scan the t3 'all' entry was diffed against is the t1 'all' entry.
    assert dtc_log.prior_matching("all", "t3", path=p)["timestamp"] == "t1"
    # Nothing earlier of the same scope.
    assert dtc_log.prior_matching("all", "t1", path=p) is None
    assert dtc_log.prior_matching("0x7EC", "t2", path=p) is None


def test_render_scan_decodes_and_shows_metadata():
    entry = dtc_log.build_scan(
        "all",
        {"0x78B": {"tx": "0x783", "protocol": "uds", "dtcs": ["B2915-00"]}},
        label="baseline",
        vehicle_states=["ready"],
        timestamp="2026-07-22T10:00:00",
    )
    text = "\n".join(dtc_log.render_scan(entry, previous=None))
    assert "DTC log: all" in text
    assert "2026-07-22T10:00:00" in text
    assert "baseline" in text
    assert "ready" in text
    assert "B2915-00" in text
    assert "no earlier scan" in text


def test_render_scan_reports_no_dtcs():
    entry = dtc_log.build_scan("0x7EC", {}, timestamp="t")
    text = "\n".join(dtc_log.render_scan(entry))
    assert "No DTCs stored" in text


def test_render_scan_includes_diff_against_previous():
    previous = dtc_log.build_scan(
        "all",
        {"0x78B": {"dtcs": ["B2915-00", "B2916-00"]}},
        timestamp="t1",
    )
    current = dtc_log.build_scan(
        "all",
        {"0x78B": {"dtcs": ["B2915-00"]}},
        timestamp="t2",
    )
    text = "\n".join(dtc_log.render_scan(current, previous))
    assert "cleared" in text
    assert "B2916-00" in text
