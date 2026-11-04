"""Tests for ``validate pids`` checks on a signal's ``send_to`` stream topic."""

import textwrap

import pytest

from canlib.commands.validate import collect_pids_validation
from canlib.commands.validate.send_to import send_to_findings


def _ecu_file(tmp_path, name, body):
    path = tmp_path / name
    path.write_text(textwrap.dedent(body))
    return path


def _bms(tmp_path, *, topic="car/stream/power", status="active", verified="true"):
    return _ecu_file(
        tmp_path,
        "bms.yaml",
        f"""\
        BMS:
          tx_id: 0x7E4
          pids:
            2101:
              status: {status}
              parameters:
                POWER:
                  expression: "B10"
                  verified: {verified}
                  send_to: "{topic}"
        """,
    )


class TestSendToFindings:
    def test_a_well_formed_shipped_topic_is_clean(self, tmp_path):
        assert send_to_findings([_bms(tmp_path)]) == ([], [])

    @pytest.mark.parametrize("topic", ["car/#", "/car", "car/", "car//x", ""])
    def test_a_malformed_topic_is_an_error(self, tmp_path, topic):
        errors, _ = send_to_findings([_bms(tmp_path, topic=topic)])
        assert len(errors) == 1 and "BMS/2101/POWER: send_to" in errors[0]

    @pytest.mark.parametrize("status", ["draft", "static", "ignored"])
    def test_a_topic_under_an_unshipped_pid_warns(self, tmp_path, status):
        errors, warnings = send_to_findings([_bms(tmp_path, status=status)])
        assert errors == []
        assert len(warnings) == 1 and f"its PID is '{status}'" in warnings[0]

    def test_a_topic_on_an_unverified_signal_warns(self, tmp_path):
        errors, warnings = send_to_findings([_bms(tmp_path, verified="false")])
        assert errors == []
        assert "--include-unverified" in warnings[0]

    def test_a_topic_shared_across_files_is_an_error(self, tmp_path):
        other = _ecu_file(
            tmp_path,
            "vcu.yaml",
            """\
            VCU:
              tx_id: 0x7E2
              pids:
                2101:
                  status: active
                  parameters:
                    SPEED:
                      expression: "B11"
                      verified: true
                      send_to: "car/stream/power"
            """,
        )
        errors, _ = send_to_findings([_bms(tmp_path), other])
        assert len(errors) == 1
        assert "also used by BMS/2101/POWER" in errors[0]

    def test_an_unshipped_signal_does_not_claim_its_topic(self, tmp_path):
        # A draft PID never ships, so its topic cannot collide with a shipped one.
        draft = _ecu_file(
            tmp_path,
            "vcu.yaml",
            """\
            VCU:
              tx_id: 0x7E2
              pids:
                2102:
                  status: draft
                  parameters:
                    OLD_POWER:
                      expression: "B11"
                      send_to: "car/stream/power"
            """,
        )
        errors, _ = send_to_findings([draft, _bms(tmp_path)])
        assert errors == []


def test_the_edit_guard_sees_a_bad_topic(tmp_path):
    # collect_pids_validation backs the single-file `canair pids` revert guard.
    errors, _, _ = collect_pids_validation([_bms(tmp_path, topic="car/+/power")])
    assert any("send_to must not contain an MQTT wildcard" in e for e in errors)
