"""The ECU schema (pids_schema.yaml): pinned to the Python vocabularies, and how it reports."""

from __future__ import annotations

import argparse
from typing import get_type_hints

import pytest

from canair.core.json_schema import load_schema_file, schema_findings
from canair.core.paths import ECUS_SCHEMA_FILE

DEFS = load_schema_file(ECUS_SCHEMA_FILE)["definitions"]


def _props(name: str) -> tuple[str, ...]:
    return tuple(DEFS[name]["properties"])


def _current(name: str) -> tuple[str, ...]:
    """The fields of a definition, without the legacy ones."""
    return tuple(k for k, v in DEFS[name]["properties"].items() if "deprecationMessage" not in v)


def _deprecated(name: str) -> set[str]:
    return {k for k, v in DEFS[name]["properties"].items() if "deprecationMessage" in v}


class TestPinnedVocabularies:
    def test_signal_types(self):
        from canair.expression.decode_value import VALID_TYPES

        assert tuple(DEFS["signal"]["properties"]["type"]["enum"]) == VALID_TYPES

    def test_wake(self):
        from canair.ecus.wake import KNOWN_METHODS
        from canair.yaml_edit.pids.params import WAKE_FIELD_ORDER

        assert set(DEFS["wake"]["properties"]["method"]["enum"]) == KNOWN_METHODS
        assert _props("wake") == WAKE_FIELD_ORDER

    def test_isotp(self):
        from canair.transport.isotp_params import ECU_ISOTP_FIELDS

        isotp = DEFS["isotp"]
        assert tuple(isotp["propertyNames"]["enum"]) == ECU_ISOTP_FIELDS
        assert _props("isotp") == ECU_ISOTP_FIELDS

    def test_identity(self):
        from canair.ecus.registry import IDENTITY_FIELDS, _EcuIdentity
        from canair.uds.protocol import ID_PROTOCOL_VALUES

        assert _props("identity") == IDENTITY_FIELDS
        assert tuple(get_type_hints(_EcuIdentity)) == (*IDENTITY_FIELDS, "can_bus")
        protocols = DEFS["identity"]["properties"]["id_protocol"]["enum"]
        assert tuple(protocols) == ID_PROTOCOL_VALUES

    def test_dtc_section_fields(self):
        from canair.ecus.dtc_definitions import SECTION_FIELDS

        assert _props("dtcs") == SECTION_FIELDS

    def test_memory_and_support_map(self):
        from canair.ecus.memory import MEMORY_FIELDS
        from canair.uds.support_map import DECL_FIELDS

        assert tuple(DEFS["ecu"]["properties"]["memory"]["properties"]) == MEMORY_FIELDS
        assert tuple(DEFS["pid"]["properties"]["support_map"]["properties"]) == DECL_FIELDS

    def test_scan_log(self):
        from canair.yaml_edit.ecu import SCAN_LOG_FIELDS

        assert _props("scan_log_entry") == SCAN_LOG_FIELDS

    def test_signal_field_order(self):
        from canair.yaml_edit.pids.params import PARAM_FIELD_ORDER

        assert set(PARAM_FIELD_ORDER) <= set(_current("signal"))

    def test_legacy_fields_are_the_ones_python_reports(self):
        from canair.ecus.signal_category import RETIRED_FIELD
        from canair.validation.pid_defs import _LEGACY_PID_FIELDS, _RENAMED_PARAM_FIELDS

        assert _deprecated("pid") == {*_LEGACY_PID_FIELDS, RETIRED_FIELD}
        assert _deprecated("signal") == {*_RENAMED_PARAM_FIELDS, RETIRED_FIELD}
        assert _deprecated("ecu") == {"availability"}
        assert _deprecated("iocontrol_entry") == {"availability", "true", "false"}
        assert _deprecated("research_entry") == {"prerequisite"}

    def test_research_choices_of_the_editor(self):
        from canair.cli import build_parser

        parser = build_parser()
        add = _subparser(_subparser(parser, "pids"), "add-research")
        choices = {a.dest: tuple(a.choices) for a in add._actions if a.choices}
        entry = DEFS["research_entry"]["properties"]
        for field in ("type", "status", "priority"):
            assert choices[field] == tuple(entry[field]["enum"]), field


def _subparser(parser: argparse.ArgumentParser, name: str) -> argparse.ArgumentParser:
    for action in parser._actions:
        if isinstance(action, argparse._SubParsersAction) and name in action.choices:
            return action.choices[name]
    raise KeyError(name)


def _check(doc: dict) -> tuple[list[str], list[str]]:
    return schema_findings(ECUS_SCHEMA_FILE, doc)


class TestFindings:
    def test_an_unknown_field_is_a_warning(self):
        doc = {"E": {"tx_id": 0x7E0, "colour": 1, "pids": {"2101": {"status": "draft", "x": 1}}}}
        errors, warnings = _check(doc)
        assert errors == []
        assert [w.split(" (allowed")[0] for w in warnings] == [
            "E: unknown field 'colour'",
            "E.pids.2101: unknown field 'x'",
        ]

    @pytest.mark.parametrize("section", ["identity", "wake", "addressing"])
    def test_an_unknown_field_is_an_error_in_a_strict_section(self, section):
        block = {"wake": {"method": "session"}}.get(section, {})
        errors, warnings = _check({"E": {"tx_id": 0x7E0, section: {**block, "bogus": 1}}})
        assert warnings == []
        assert [e.split(" (allowed")[0] for e in errors] == [f"E.{section}: unknown field 'bogus'"]

    def test_a_legacy_field_is_left_to_python(self):
        doc = {"E": {"tx_id": 0x7E0, "pids": {"2101": {"status": "draft", "static": True}}}}
        assert _check(doc) == ([], [])

    def test_a_legacy_field_is_not_offered_as_allowed(self):
        _, warnings = _check({"E": {"tx_id": 0x7E0, "pids": {"2101": {"status": "x", "y": 1}}}})
        assert "static" not in warnings[0] and "status" in warnings[0]

    def test_slow_needs_a_period(self):
        errors, _ = _check({"E": {"tx_id": 0x7E0, "pids": {"21F2": {"status": "slow"}}}})
        assert errors[0].startswith("E.pids.21F2: status 'slow' needs an explicit period")

    def test_a_count_on_a_variable_length_pid(self):
        pid = {"status": "active", "variable_length": True, "response_frames": 4}
        errors, _ = _check({"E": {"tx_id": 0x7E0, "pids": {"2101": pid}}})
        assert errors == [
            "E.pids.2101.response_frames: cannot be set with variable_length: true (a "
            "variable-length response has no fixed frame count)"
        ]

    def test_a_date_reads_as_text(self):
        import datetime

        entry = {"service": 0x22, "date": datetime.date(2026, 7, 22)}
        assert _check({"E": {"tx_id": 0x7E0, "scan_log": [entry]}}) == ([], [])

    def test_an_unquoted_on_key_is_left_to_python(self):
        entry = {"label": "x", True: "2FBC0103", False: "2FBC0100"}
        assert _check({"E": {"tx_id": 0x7E0, "iocontrol": {"BC01": entry}}}) == ([], [])
