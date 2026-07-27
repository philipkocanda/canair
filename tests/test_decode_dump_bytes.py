"""Tests for decode.py --dump-bytes byte-matrix export (_dump_bytes)."""

import io
import json
from contextlib import redirect_stdout
from datetime import datetime

from canlib.commands import decode as decode_script
from canlib.notation import ByteNotation


def _result(payload: str, date: str = "2026-07-22", time: str = "09:00:00.000") -> dict:
    """A fake all_results entry wrapping one capture with a raw payload."""
    return {
        "capture": {
            "payload": payload,
            "date": date,
            "time": time,
            "vehicle_states": ["ready"],
        },
        "decoded": {},
    }


def _dump(results, **kw) -> str:
    defaults = {
        "as_json": False,
        "include_pci": False,
        "notation": ByteNotation.WICAN,
        "sub_bytes": 1,
    }
    defaults.update(kw)
    buf = io.StringIO()
    with redirect_stdout(buf):
        rc = decode_script._dump_bytes(results, "BMS", "2101", **defaults)
    assert rc == 0
    return buf.getvalue()


class TestDumpBytesCSV:
    def test_header_and_row(self):
        # 6101ABCD -> WiCAN frame 04 61 01 AB CD: B0/B1 are framing/header,
        # B2 = PID echo (0x01), B3 = first data byte (0xAB).
        out = _dump([_result("6101ABCD")])
        lines = out.strip().splitlines()
        header = lines[0].split(",")
        assert header[:3] == ["time", "ecu", "pid"]
        # PCI byte B0 is skipped by default; data columns present.
        assert "B0" not in header
        assert "B3" in header
        row = lines[1].split(",")
        assert row[1:3] == ["BMS", "2101"]
        b3 = row[header.index("B3")]
        assert int(b3) == 0xAB

    def test_pci_skipped_by_default_included_on_flag(self):
        default = _dump([_result("6101ABCD")]).splitlines()[0].split(",")
        with_pci = _dump([_result("6101ABCD")], include_pci=True).splitlines()[0].split(",")
        assert "B0" not in default
        assert "B0" in with_pci

    def test_uses_entry_datetime_when_timed(self):
        out = _dump([_result("6101ABCD", time="09:06:37.007")])
        row = out.strip().splitlines()[1]
        assert row.startswith(datetime(2026, 7, 22, 9, 6, 37, 7000).isoformat())

    def test_ragged_rows_pad_blank(self):
        # A shorter capture leaves trailing cells blank rather than erroring.
        out = _dump([_result("6101ABCDEF"), _result("6101AB")])
        lines = out.strip().splitlines()
        header = lines[0].split(",")
        short = lines[2].split(",")
        # last data column is blank for the short payload
        assert short[-1] == ""
        assert len(short) == len(header)


class TestDumpBytesJSON:
    def test_shape(self):
        out = _dump([_result("6101ABCD")], as_json=True)
        doc = json.loads(out)
        assert doc["ecu"] == "BMS"
        assert doc["pid"] == "2101"
        assert doc["include_pci"] is False
        assert "B3" in doc["columns"]
        assert doc["rows"][0]["bytes"]["B3"] == 0xAB
        assert doc["rows"][0]["vehicle_states"] == ["ready"]

    def test_ragged_pads_null(self):
        out = _dump([_result("6101ABCDEF"), _result("6101AB")], as_json=True)
        doc = json.loads(out)
        last = doc["columns"][-1]
        assert doc["rows"][1]["bytes"][last] is None

    def test_notation_relabels_columns(self):
        out = _dump([_result("6101ABCD")], as_json=True, notation=ByteNotation.ISOTP)
        doc = json.loads(out)
        assert doc["notation"] == "isotp"
        # ISO-TP labels use the i-prefix, never WiCAN Bnn.
        assert all(not c.startswith("B") for c in doc["columns"])
