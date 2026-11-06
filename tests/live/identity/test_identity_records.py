"""Tests for canair.live.identity.records — the labels, the make rows and the fingerprint order."""

import json

import pytest

from canair.cli.profile_context import active, set_active
from canair.live.identity import decode as idec
from canair.live.identity import read as ident
from canair.live.identity.records import (
    KWP_IDENTITY_RECORDS,
    UDS_IDENTITY_DIDS,
    records_for,
    sw_unit_records,
)
from canair.live.identity.software import read_software_version, software_records
from canair.profile.names import make_of
from tests.support.fakes import FakeTerminal, nrc, ok

HK = ("hyundai", "kia")

# Every row, pinned: a label or decode change must be deliberate. Labels follow ISO 14229-1
# Table C.1 and SSF 14230-3 Table 6.6.3 (plans/2026-11-06-identity-record-labels.md).
UDS_TABLE = [
    ("F190", "VIN", "ascii", ()),
    ("F187", "VM spare part number", "ascii", ()),
    ("F188", "VM ECU software number", "ascii", ()),
    ("F189", "VM ECU software version", "ascii", ()),
    ("F18A", "System supplier ID", "ascii", ()),
    ("F18B", "ECU manufacturing date", "date", ()),
    ("F18C", "ECU serial number", "ascii", ()),
    ("F18D", "Supported functional units", "hex", ()),
    ("F18E", "VM kit assembly part number", "ascii", ()),
    ("F186", "Active diagnostic session", "session", ()),
    ("F191", "VM ECU hardware number", "ascii", ()),
    ("F192", "Supplier ECU HW number", "ascii", ()),
    ("F193", "Supplier ECU HW version", "ascii", ()),
    ("F194", "Supplier ECU SW number", "ascii", ()),
    ("F195", "Supplier ECU SW version", "ascii", ()),
    ("F196", "Exhaust regulation / type approval no.", "ascii", ()),
    ("F197", "System name / engine type", "ascii", ()),
    ("F199", "Programming date", "date", ()),
    ("F19B", "Calibration date", "date", ()),
    ("F19D", "ECU installation date", "date", ()),
    ("F180", "Boot SW identification", "modules", ()),
    ("F181", "Application SW identification", "modules", ()),
    ("F182", "Application data identification", "modules", ()),
    ("F183", "Boot SW fingerprint", "hex", ()),
    ("F184", "Application SW fingerprint", "hex", ()),
    ("F185", "Application data fingerprint", "hex", ()),
    ("F100", "Manufacturer-specific F100", "ascii", ()),
    ("F100", "CAN DB version", "ascii", HK),
    ("F101", "Manufacturer-specific F101", "ascii", ()),
    ("F110", "Manufacturer-specific F110", "ascii", ()),
    ("F17E", "Manufacturer-specific F17E", "ascii", ()),
    ("F1A0", "Manufacturer-specific F1A0", "ascii", ()),
    ("F1A0", "Software version", "ascii", HK),
    ("F1A1", "Supplier code", "ascii", HK),
    ("F1A2", "Manufacturer-specific F1A2", "ascii", ()),
    ("F1A4", "Manufacturer-specific F1A4", "ascii", ()),
    ("F1B0", "Number of SW units", "count", HK),
]

KWP_TABLE = [
    ("80", "ECU identification data table", "auto", ()),
    ("86", "Manufacturer-specific 86", "auto", ()),
    ("87", "VM spare part number", "ascii", ()),
    ("88", "VM ECU software number", "ascii", ()),
    ("89", "VM ECU software version", "ascii", ()),
    ("8A", "System supplier", "ascii", ()),
    ("8B", "ECU manufacturing date", "date", ()),
    ("8C", "ECU serial number", "ascii", ()),
    ("8C", "Module code", "ascii", HK),
    ("8D", "Supplier-specific 8D", "ascii", ()),
    ("8E", "Supplier-specific 8E", "ascii", ()),
    ("90", "VIN", "ascii", ()),
    ("91", "VM ECU hardware number", "ascii", ()),
    ("92", "Supplier ECU HW number", "ascii", ()),
    ("93", "Supplier ECU HW version", "ascii", ()),
    ("94", "Supplier ECU SW number", "ascii", ()),
    ("95", "Supplier ECU SW version", "ascii", ()),
    ("96", "Exhaust regulation / type approval no.", "ascii", ()),
    ("97", "System name / engine type", "ascii", ()),
    ("98", "Repair shop code / tester serial", "ascii", ()),
    ("99", "Programming date", "date", ()),
    ("9A", "Calibration shop code / equipment serial", "ascii", ()),
    ("9B", "Calibration date", "date", ()),
    ("9D", "ECU installation date", "date", ()),
]


class TestTables:
    def test_uds_rows(self):
        assert [tuple(r) for r in UDS_IDENTITY_DIDS] == UDS_TABLE

    def test_kwp_rows(self):
        assert [tuple(r) for r in KWP_IDENTITY_RECORDS] == KWP_TABLE

    def test_every_decode_hint_is_known(self):
        hints = {"ascii", "date", "auto", *idec._STRUCTURED}
        for record in (*UDS_IDENTITY_DIDS, *KWP_IDENTITY_RECORDS):
            assert record.decode in hints, record


class TestMakeRows:
    def test_a_make_row_replaces_the_generic_one(self):
        hk = {r.id: r.label for r in records_for("uds", "hyundai")}
        assert hk["F100"] == "CAN DB version"
        assert hk["F1A0"] == "Software version"
        assert {r.id: r.label for r in records_for("kwp", "kia")}["8C"] == "Module code"

    def test_another_make_reads_the_generic_rows_and_no_make_only_row(self):
        other = records_for("uds", "nissan")
        labels = {r.id: r.label for r in other}
        assert labels["F100"] == "Manufacturer-specific F100"
        assert "F1A1" not in labels
        assert "F1B0" not in labels
        assert {r.id: r.label for r in records_for("kwp", None)}["8C"] == "ECU serial number"

    def test_each_identifier_is_read_once(self):
        for protocol in ("uds", "kwp"):
            for make in ("hyundai", "nissan", None):
                ids = [r.id for r in records_for(protocol, make)]
                assert len(ids) == len(set(ids)), (protocol, make)

    def test_the_make_is_the_first_part_of_the_profile_name(self):
        assert make_of("hyundai-ioniq-ae-ev") == "hyundai"
        assert make_of("kia-niro-de-ev") == "kia"
        assert make_of("mycar") == "mycar"

    @pytest.mark.asyncio
    async def test_a_make_row_is_read_only_on_that_makes_profile(self, monkeypatch):
        monkeypatch.setattr(idec, "ecu_protocol", lambda tx, **__: "uds")
        for name, read in (("hyundai-ioniq-ae-ev", True), ("nissan-leaf-ze1-ev", False)):
            set_active(name)
            term = FakeTerminal({"22F190": ok("62F190414243")}, default=nrc(0x31))
            await ident.mode_identity(
                term, 0x7A0, session=False, wake=False, as_json=True, profile=active()
            )
            assert ("22F1A1" in term.sent) is read, name


class TestSoftwareUnits:
    def test_unit_rows(self):
        assert [r.id for r in sw_unit_records(3)] == ["F1B1", "F1B2", "F1B3"]
        assert sw_unit_records(3)[0].label == "SW unit 1 version"
        assert sw_unit_records(0) == []
        assert sw_unit_records(200)[-1].id == "F1BF"

    @pytest.mark.asyncio
    async def test_f1b0_limits_the_unit_reads(self, monkeypatch, capsys):
        monkeypatch.setattr(idec, "ecu_protocol", lambda tx, **__: "uds")
        replies = {
            "22F190": ok("62F190414243"),
            "22F1B0": ok("62F1B002"),
            "22F1B1": ok("62F1B1" + b"1.00".hex()),
            "22F1B2": ok("62F1B2" + b"2.01".hex()),
        }
        term = FakeTerminal(replies, default=nrc(0x31))
        await ident.mode_identity(
            term, 0x7A0, session=False, wake=False, as_json=True, profile=active()
        )
        units = [r for r in term.sent if r.startswith("22F1B")]
        assert units == ["22F1B0", "22F1B1", "22F1B2"]
        data = json.loads(capsys.readouterr().out)
        decoded = {r["did"]: (r["label"], r["decoded"]) for r in data["results"]}
        assert decoded["F1B0"] == ("Number of SW units", "2")
        assert decoded["F1B2"] == ("SW unit 2 version", "2.01")
        assert data["summary"]["not_asked"] == 0

    @pytest.mark.asyncio
    async def test_no_unit_is_read_when_f1b0_is_refused(self, monkeypatch):
        monkeypatch.setattr(idec, "ecu_protocol", lambda tx, **__: "uds")
        term = FakeTerminal({"22F190": ok("62F190414243")}, default=nrc(0x31))
        await ident.mode_identity(
            term, 0x7A0, session=False, wake=False, as_json=True, profile=active()
        )
        assert "22F1B1" not in term.sent


class TestSoftwareFingerprintOrder:
    def test_uds(self):
        assert software_records("uds", "hyundai") == ("F189", "F195", "F1A0", "F194")
        assert software_records("uds", "nissan") == ("F189", "F195", "F1A0", "F194")

    def test_kwp_reads_89_first_and_8c_only_on_hyundai_kia(self):
        assert software_records("kwp", "nissan") == ("89", "95", "88", "8D")
        assert software_records("kwp", "hyundai") == ("89", "95", "88", "8D", "8C")
        assert software_records("kwp", "kia")[-1] == "8C"

    @pytest.mark.asyncio
    async def test_8c_is_the_reading_on_hyundai_kia_only(self):
        replies = {"1A8C": ok("5A8C" + b"AEEOBC51".hex())}
        hk = await read_software_version(FakeTerminal(replies), 0x7E5, "kwp", make="hyundai")
        assert hk == "8C:AEEOBC51"
        assert await read_software_version(FakeTerminal(replies), 0x7E5, "kwp") is None
