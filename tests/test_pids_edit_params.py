"""Tests for canlib.pids_edit parameter + research editing (RE workflow)."""

import textwrap

import pytest
import yaml

from canlib.pids_edit import (
    PidsEditError,
    add_research_entry,
    set_research_status,
    upsert_parameter,
)


@pytest.fixture
def pids_dir(tmp_path):
    (tmp_path / "_meta.yaml").write_text('car_model: "Test"\ninit: "ATSP6;"\n')
    (tmp_path / "test.yaml").write_text(
        textwrap.dedent(
            """\
            # Header comment that must survive edits
            TESTECU:
              tx_id: 0x7E9
              pids:
                2101:
                  period: 5000
                  enabled: true
                  parameters:
                    EXISTING:
                      expression: "B3"
                      unit: "%"
                      verified: true
                      notes: >
                        original note
                2102:
                  enabled: false
                  parameters: {}
                2103:
                  notes: "pid with no parameters key"
              research:
                - type: decode
                  target: "2102"
                  status: captured
                  priority: P1
            """
        )
    )
    return tmp_path


def _load(pids_dir):
    return yaml.safe_load((pids_dir / "test.yaml").read_text())


def _params(pids_dir, pid):
    ecu = _load(pids_dir)["TESTECU"]
    block = next(v for k, v in ecu["pids"].items() if str(k).upper() == str(pid).upper())
    return (block or {}).get("parameters") or {}


class TestUpsertParameterNew:
    def test_add_into_existing_parameters_block(self, pids_dir):
        upsert_parameter(
            "TESTECU", "2101", "NEWP", "[S10:S11]/100",
            unit="Nm", verified=False, source="test", pids_dir=pids_dir,
        )
        params = _params(pids_dir, "2101")
        assert "EXISTING" in params  # preserved
        assert params["NEWP"]["expression"] == "[S10:S11]/100"
        assert params["NEWP"]["unit"] == "Nm"
        assert params["NEWP"]["verified"] is False
        # Comment header preserved.
        assert "Header comment that must survive" in (pids_dir / "test.yaml").read_text()

    def test_convert_inline_empty_map(self, pids_dir):
        upsert_parameter("TESTECU", "2102", "FIRST", "B9", unit="V", pids_dir=pids_dir)
        params = _params(pids_dir, "2102")
        assert params == {"FIRST": {"expression": "B9", "unit": "V"}}

    def test_add_when_no_parameters_key(self, pids_dir):
        upsert_parameter("TESTECU", "2103", "X", "B4", pids_dir=pids_dir)
        assert _params(pids_dir, "2103")["X"]["expression"] == "B4"
        # sibling field preserved
        ecu = _load(pids_dir)["TESTECU"]
        blk = next(v for k, v in ecu["pids"].items() if str(k) == "2103")
        assert blk["notes"] == "pid with no parameters key"

    def test_create_new_pid_block(self, pids_dir):
        upsert_parameter(
            "TESTECU", "22C00B", "TPMS", "B7", unit="kPa", min="0", max="500",
            pids_dir=pids_dir,
        )
        params = _params(pids_dir, "22C00B")
        assert params["TPMS"]["expression"] == "B7"
        # min/max rendered as strings per schema convention
        assert params["TPMS"]["min"] == "0" and params["TPMS"]["max"] == "500"

    def test_expression_with_brackets_is_quoted(self, pids_dir):
        upsert_parameter("TESTECU", "2101", "RANGED", "[S4:S5]", pids_dir=pids_dir)
        raw = (pids_dir / "test.yaml").read_text()
        assert 'expression: "[S4:S5]"' in raw

    def test_notes_render_as_block_scalar(self, pids_dir):
        upsert_parameter("TESTECU", "2101", "WITHNOTE", "B3",
                         notes="line one\nline two", pids_dir=pids_dir)
        assert _params(pids_dir, "2101")["WITHNOTE"]["notes"].strip().startswith("line one")


class TestUpsertParameterUpdate:
    def test_update_single_field_preserves_others(self, pids_dir):
        upsert_parameter("TESTECU", "2101", "EXISTING", "B5", pids_dir=pids_dir)
        p = _params(pids_dir, "2101")["EXISTING"]
        assert p["expression"] == "B5"     # changed
        assert p["unit"] == "%"            # preserved
        assert p["verified"] is True       # preserved
        assert "original note" in p["notes"]  # preserved

    def test_update_flips_verified(self, pids_dir):
        upsert_parameter("TESTECU", "2101", "EXISTING", "B3", verified=False, pids_dir=pids_dir)
        assert _params(pids_dir, "2101")["EXISTING"]["verified"] is False


class TestUpsertValidation:
    def test_bad_name_raises(self, pids_dir):
        with pytest.raises(PidsEditError):
            upsert_parameter("TESTECU", "2101", "1BAD NAME", "B3", pids_dir=pids_dir)

    def test_empty_expression_raises(self, pids_dir):
        with pytest.raises(PidsEditError):
            upsert_parameter("TESTECU", "2101", "P", "  ", pids_dir=pids_dir)

    def test_unknown_ecu_raises(self, pids_dir):
        with pytest.raises(PidsEditError):
            upsert_parameter("NOPE", "2101", "P", "B3", pids_dir=pids_dir)

    def test_result_still_valid_yaml_and_loads(self, pids_dir):
        upsert_parameter("TESTECU", "2101", "NEWP", "B6", unit="A", pids_dir=pids_dir)
        # Whole file must still round-trip through the real loader.
        from canlib.pids import build_ecu_index, load_pids
        idx = build_ecu_index(load_pids(pids_dir))
        assert "NEWP" in idx["TESTECU"]["pids"]["2101"]["parameters"]


class TestResearch:
    def test_add_entry_to_existing_section(self, pids_dir):
        add_research_entry(
            "TESTECU", type="scan", target="22 E001-E010", status="pending",
            priority="P1", prerequisite=["acc"], notes="cross-ref", pids_dir=pids_dir,
        )
        research = _load(pids_dir)["TESTECU"]["research"]
        assert len(research) == 2
        new = next(e for e in research if e["target"] == "22 E001-E010")
        assert new["type"] == "scan" and new["prerequisite"] == ["acc"]

    def test_add_entry_creates_section(self, pids_dir):
        # Remove research section first by rewriting a section-less ECU.
        (pids_dir / "test2.yaml").write_text("OTHER:\n  tx_id: 0x700\n  pids: {}\n")
        add_research_entry("OTHER", type="verify", target="B002", status="pending",
                           pids_dir=pids_dir)
        assert _load2(pids_dir)["OTHER"]["research"][0]["target"] == "B002"

    def test_add_entry_requires_core_fields(self, pids_dir):
        with pytest.raises(PidsEditError):
            add_research_entry("TESTECU", type="scan", target="", status="pending",
                               pids_dir=pids_dir)

    def test_set_status(self, pids_dir):
        set_research_status("TESTECU", "2102", "done", pids_dir=pids_dir)
        research = _load(pids_dir)["TESTECU"]["research"]
        assert research[0]["status"] == "done"

    def test_set_status_not_found(self, pids_dir):
        with pytest.raises(PidsEditError):
            set_research_status("TESTECU", "9999", "done", pids_dir=pids_dir)


def _load2(pids_dir):
    return yaml.safe_load((pids_dir / "test2.yaml").read_text())
