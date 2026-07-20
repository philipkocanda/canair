"""Tests for capture session builders and metadata resolution."""

from unittest.mock import patch

import yaml

from canlib.captures import (
    build_query_session,
    resolve_metadata,
    save_session,
)


class TestResolveMetadata:
    def test_label_given_is_noninteractive(self):
        """When a label is supplied, no prompt is shown and flags are used verbatim."""
        # input() would raise if called — proving non-interactive.
        with patch("builtins.input", side_effect=AssertionError("should not prompt")):
            meta = resolve_metadata("My label", "ready, parked", "some notes")
        assert meta == ("My label", "ready, parked", "some notes")

    def test_label_given_defaults_empty_state_notes(self):
        with patch("builtins.input", side_effect=AssertionError("should not prompt")):
            meta = resolve_metadata("Only label", None, None)
        assert meta == ("Only label", "", "")

    def test_no_label_falls_back_to_prompt(self):
        with patch("builtins.input", side_effect=["Prompted", "charging", "n"]):
            meta = resolve_metadata(None, None, None, suggested_label="sugg")
        assert meta == ("Prompted", "charging", "n")

    def test_no_label_prompt_cancelled(self):
        with patch("builtins.input", side_effect=KeyboardInterrupt):
            meta = resolve_metadata(None, None, None)
        assert meta is None


class TestBuildQuerySession:
    def test_groups_and_uppercases(self):
        # ecu_ref is the ECU CAN response address (RX = request TX + 8).
        results = [
            ("0x7EB", "2102", "6102aabb", ""),   # MCU (0x7E3 + 8)
            ("0x7EA", "2101", "6101ccdd", "12:00:01"),  # VCU (0x7E2 + 8)
        ]
        s = build_query_session(results, "lbl", "ready, parked", "notes here")
        assert s["label"] == "lbl"
        assert s["state"] == "ready, parked"
        assert s["notes"] == "notes here"
        assert "date" in s
        assert s["captures"][0] == {"ecu": "0x7EB", "pid": "2102", "payload": "6102AABB"}
        # time preserved when present
        assert s["captures"][1]["time"] == "12:00:01"
        assert s["captures"][1]["payload"] == "6101CCDD"

    def test_empty_state_notes_omitted(self):
        s = build_query_session([("0x7EC", "2101", "6101", "")], "l", "", "")  # BMS
        assert "state" not in s
        assert "notes" not in s

    def test_roundtrips_and_appends_via_save_session(self, tmp_path):
        results = [("0x7EB", "2102", "6102AABB", "")]  # MCU
        s = build_query_session(results, "Live ref", "ready, parked", "18C")
        save_session(s, tmp_path)
        files = list(tmp_path.glob("*.yaml"))
        assert len(files) == 1
        data = yaml.safe_load(files[0].read_text())
        assert data["sessions"][0]["label"] == "Live ref"
        assert data["sessions"][0]["captures"][0]["payload"] == "6102AABB"
