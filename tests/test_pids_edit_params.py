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
            "TESTECU",
            "2101",
            "NEWP",
            "[S10:S11]/100",
            unit="Nm",
            verified=False,
            source="test",
            pids_dir=pids_dir,
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
            "TESTECU",
            "22C00B",
            "TPMS",
            "B7",
            unit="kPa",
            min="0",
            max="500",
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
        upsert_parameter(
            "TESTECU", "2101", "WITHNOTE", "B3", notes="line one\nline two", pids_dir=pids_dir
        )
        assert _params(pids_dir, "2101")["WITHNOTE"]["notes"].strip().startswith("line one")


class TestUpsertCreatesPidsSection:
    """upsert-param is the create path: it scaffolds a missing pids: section
    (e.g. on a freshly registered, PID-less ECU)."""

    def _write(self, tmp_path, body):
        (tmp_path / "_meta.yaml").write_text('car_model: "Test"\ninit: "ATSP6;"\n')
        (tmp_path / "e.yaml").write_text(textwrap.dedent(body))
        return tmp_path

    def _ecu(self, d):
        return yaml.safe_load((d / "e.yaml").read_text())["NEWECU"]

    def test_no_pids_section_at_all(self, tmp_path):
        d = self._write(
            tmp_path,
            """\
            # keep me
            NEWECU:
              tx_id: 0x7C6
              identity:
                description: Cluster
            """,
        )
        upsert_parameter("NEWECU", "B002", "ODOMETER", "B6*65536+B7", unit="km", pids_dir=d)
        ecu = self._ecu(d)
        assert ecu["pids"]["B002"]["status"] == "active"
        assert ecu["pids"]["B002"]["parameters"]["ODOMETER"]["expression"] == "B6*65536+B7"
        assert ecu["identity"]["description"] == "Cluster"  # sibling preserved
        assert "# keep me" in (d / "e.yaml").read_text()  # comment preserved

    def test_empty_block_form_pids(self, tmp_path):
        d = self._write(
            tmp_path,
            """\
            NEWECU:
              tx_id: 0x7C6
              pids:
            """,
        )
        upsert_parameter("NEWECU", "B002", "ODO", "B6", unit="km", pids_dir=d)
        assert self._ecu(d)["pids"]["B002"]["parameters"]["ODO"]["expression"] == "B6"

    def test_inline_empty_map_pids(self, tmp_path):
        d = self._write(
            tmp_path,
            """\
            NEWECU:
              tx_id: 0x7C6
              pids: {}
            """,
        )
        upsert_parameter("NEWECU", "B002", "ODO", "B6", unit="km", pids_dir=d)
        assert self._ecu(d)["pids"]["B002"]["parameters"]["ODO"]["expression"] == "B6"

    def test_result_round_trips_through_loader(self, tmp_path):
        d = self._write(
            tmp_path,
            """\
            NEWECU:
              tx_id: 0x7C6
              identity:
                description: Cluster
            """,
        )
        upsert_parameter("NEWECU", "B002", "ODO", "B6", unit="km", pids_dir=d)
        from canlib.pids import build_ecu_index, load_pids

        idx = build_ecu_index(load_pids(d))
        assert "NEWECU" in idx


class TestUpsertParameterUpdate:
    def test_update_single_field_preserves_others(self, pids_dir):
        upsert_parameter("TESTECU", "2101", "EXISTING", "B5", pids_dir=pids_dir)
        p = _params(pids_dir, "2101")["EXISTING"]
        assert p["expression"] == "B5"  # changed
        assert p["unit"] == "%"  # preserved
        assert p["verified"] is True  # preserved
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
            "TESTECU",
            type="scan",
            target="22 E001-E010",
            status="pending",
            priority="P1",
            vehicle_states=["acc"],
            notes="cross-ref",
            pids_dir=pids_dir,
        )
        research = _load(pids_dir)["TESTECU"]["research"]
        assert len(research) == 2
        new = next(e for e in research if e["target"] == "22 E001-E010")
        assert new["type"] == "scan" and new["vehicle_states"] == ["acc"]

    def test_add_entry_creates_section(self, pids_dir):
        # Remove research section first by rewriting a section-less ECU.
        (pids_dir / "test2.yaml").write_text("OTHER:\n  tx_id: 0x700\n  pids: {}\n")
        add_research_entry(
            "OTHER", type="verify", target="B002", status="pending", pids_dir=pids_dir
        )
        assert _load2(pids_dir)["OTHER"]["research"][0]["target"] == "B002"

    def test_add_entry_requires_core_fields(self, pids_dir):
        with pytest.raises(PidsEditError):
            add_research_entry(
                "TESTECU", type="scan", target="", status="pending", pids_dir=pids_dir
            )

    def test_numeric_target_stays_string(self, pids_dir):
        # Regression: all-digit targets (e.g. "220101") must be quoted so YAML
        # re-parses them as strings, not ints — otherwise the post-edit checker
        # (str target vs int parsed value) fails with "research entry missing".
        add_research_entry(
            "TESTECU",
            type="decode",
            target="220101",
            status="captured",
            pids_dir=pids_dir,
        )
        raw = (pids_dir / "test.yaml").read_text()
        assert 'target: "220101"' in raw
        research = _load(pids_dir)["TESTECU"]["research"]
        new = next(e for e in research if e["target"] == "220101")
        assert isinstance(new["target"], str)
        assert new["type"] == "decode"

    def test_capture_protocol_field(self, pids_dir):
        add_research_entry(
            "TESTECU",
            type="decode",
            target="22C101",
            status="captured",
            capture_protocol="Hold wheel at centre, full-lock each way.",
            pids_dir=pids_dir,
        )
        research = _load(pids_dir)["TESTECU"]["research"]
        new = next(e for e in research if e["target"] == "22C101")
        assert "full-lock" in new["capture_protocol"]

    def test_set_status(self, pids_dir):
        set_research_status("TESTECU", "2102", "done", pids_dir=pids_dir)
        research = _load(pids_dir)["TESTECU"]["research"]
        assert research[0]["status"] == "done"

    def test_set_status_not_found(self, pids_dir):
        with pytest.raises(PidsEditError):
            set_research_status("TESTECU", "9999", "done", pids_dir=pids_dir)

    def test_add_entry_auto_timestamps(self, pids_dir):
        import datetime

        today = datetime.date.today().isoformat()
        add_research_entry(
            "TESTECU",
            type="scan",
            target="22 F001-F010",
            status="pending",
            pids_dir=pids_dir,
        )
        new = next(
            e for e in _load(pids_dir)["TESTECU"]["research"] if e["target"] == "22 F001-F010"
        )
        assert new["created"] == today
        assert new["updated"] == today

    def test_add_entry_explicit_timestamps_override(self, pids_dir):
        add_research_entry(
            "TESTECU",
            type="scan",
            target="22 G001",
            status="pending",
            created="2020-01-01",
            updated="2020-02-02",
            pids_dir=pids_dir,
        )
        new = next(e for e in _load(pids_dir)["TESTECU"]["research"] if e["target"] == "22 G001")
        assert new["created"] == "2020-01-01"
        assert new["updated"] == "2020-02-02"

    def test_set_status_bumps_updated(self, pids_dir):
        # Seed an entry with a stale updated date, then transition its status.
        import datetime

        add_research_entry(
            "TESTECU",
            type="decode",
            target="22H001",
            status="captured",
            updated="2020-01-01",
            pids_dir=pids_dir,
        )
        set_research_status("TESTECU", "22H001", "done", type="decode", pids_dir=pids_dir)
        new = next(e for e in _load(pids_dir)["TESTECU"]["research"] if e["target"] == "22H001")
        assert new["status"] == "done"
        assert new["updated"] == datetime.date.today().isoformat()

    def test_set_status_adds_updated_when_absent(self, pids_dir):
        # The pre-seeded fixture entry has no `updated` field; set-status must add it.
        import datetime

        set_research_status("TESTECU", "2102", "done", pids_dir=pids_dir)
        entry = _load(pids_dir)["TESTECU"]["research"][0]
        assert entry["status"] == "done"
        assert entry["updated"] == datetime.date.today().isoformat()


def _load2(pids_dir):
    return yaml.safe_load((pids_dir / "test2.yaml").read_text())


class TestRenameParameter:
    def test_rename_preserves_fields(self, pids_dir):
        from canlib.pids_edit import rename_parameter

        rename_parameter("TESTECU", "2101", "EXISTING", "RENAMED", pids_dir=pids_dir)
        params = _load(pids_dir)["TESTECU"]["pids"][2101]["parameters"]
        assert "EXISTING" not in params
        assert params["RENAMED"]["expression"] == "B3"
        assert params["RENAMED"]["unit"] == "%"
        assert params["RENAMED"]["verified"] is True

    def test_rename_preserves_header_comment(self, pids_dir):
        from canlib.pids_edit import rename_parameter

        rename_parameter("TESTECU", "2101", "EXISTING", "RENAMED", pids_dir=pids_dir)
        assert "Header comment that must survive edits" in (pids_dir / "test.yaml").read_text()

    def test_rename_missing_raises(self, pids_dir):
        from canlib.pids_edit import rename_parameter

        with pytest.raises(PidsEditError, match="not found"):
            rename_parameter("TESTECU", "2101", "NOPE", "X", pids_dir=pids_dir)

    def test_rename_collision_raises(self, pids_dir):
        from canlib.pids_edit import rename_parameter, upsert_parameter

        upsert_parameter("TESTECU", "2101", "OTHER", "B4", pids_dir=pids_dir)
        with pytest.raises(PidsEditError, match="already exists"):
            rename_parameter("TESTECU", "2101", "EXISTING", "OTHER", pids_dir=pids_dir)

    def test_rename_invalid_name_raises(self, pids_dir):
        from canlib.pids_edit import rename_parameter

        with pytest.raises(PidsEditError, match="invalid parameter name"):
            rename_parameter("TESTECU", "2101", "EXISTING", "bad name", pids_dir=pids_dir)


class TestDeleteParameter:
    def test_delete_removes(self, pids_dir):
        from canlib.pids_edit import delete_parameter

        delete_parameter("TESTECU", "2101", "EXISTING", pids_dir=pids_dir)
        params = _load(pids_dir)["TESTECU"]["pids"][2101]["parameters"] or {}
        assert "EXISTING" not in params

    def test_delete_missing_raises(self, pids_dir):
        from canlib.pids_edit import delete_parameter

        with pytest.raises(PidsEditError, match="not found"):
            delete_parameter("TESTECU", "2101", "NOPE", pids_dir=pids_dir)

    def test_delete_preserves_other_params(self, pids_dir):
        from canlib.pids_edit import delete_parameter, upsert_parameter

        upsert_parameter("TESTECU", "2101", "KEEP", "B5", pids_dir=pids_dir)
        delete_parameter("TESTECU", "2101", "EXISTING", pids_dir=pids_dir)
        params = _load(pids_dir)["TESTECU"]["pids"][2101]["parameters"]
        assert "KEEP" in params and "EXISTING" not in params

    def test_delete_preserves_following_pid_separator(self, pids_dir):
        # Deleting a PID's last param must not collapse the blank line separating
        # it from the next PID header (the whitespace-drift bug this guards).
        from canlib.pids_edit import delete_parameter

        # Insert a blank line before 2102 (mirrors real files' spacing).
        text = (pids_dir / "test.yaml").read_text().replace("\n    2102:", "\n\n    2102:", 1)
        (pids_dir / "test.yaml").write_text(text)
        assert "\n\n    2102:" in text  # precondition
        delete_parameter("TESTECU", "2101", "EXISTING", pids_dir=pids_dir)
        after = (pids_dir / "test.yaml").read_text()
        assert "\n\n    2102:" in after  # blank separator preserved
        assert yaml.safe_load(after)["TESTECU"]["pids"][2102] == {
            "enabled": False,
            "parameters": {},
        }
