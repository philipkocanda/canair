"""Tests for the capture echo-mismatch lint (canlib.commands.validate).

Cross-checks each capture's stored ``pid`` against its ``payload``'s SID +
identifier echo, flagging stale/misfiled frames (e.g. a 6101 response filed
under a 2102 request — the ELM327 leaks a previous request's late response into
the next read). Reported as a soft warning, never an error.

Also covers the non-hex payload lint (``_capture_nonhex_warnings``), which
flags payloads that aren't valid UDS byte strings (error strings, free text,
mixed hex+ASCII).
"""

import textwrap

from canlib.commands.validate import _capture_echo_warnings, _capture_nonhex_warnings


def _write(tmp_path, body: str):
    p = tmp_path / "2026-07-19.yaml"
    p.write_text(textwrap.dedent(body))
    return p


def test_flags_pid_echo_mismatch(tmp_path):
    path = _write(
        tmp_path,
        """
        sessions:
          - date: "2026-07-19"
            captures:
              - ecu: "0x7EB"
                pid: "2102"
                payload: "6101FFE0000009"
                time: "16:13:04"
        """,
    )
    warnings = _capture_echo_warnings(path)
    assert len(warnings) == 1
    assert "2102" in warnings[0]
    assert "16:13:04" in warnings[0]


def test_clean_capture_no_warning(tmp_path):
    path = _write(
        tmp_path,
        """
        sessions:
          - date: "2026-07-19"
            captures:
              - ecu: "0x7EB"
                pid: "2102"
                payload: "6102F8F8000001"
                time: "16:10:00"
        """,
    )
    assert _capture_echo_warnings(path) == []


def test_hk_identity_offset_not_flagged(tmp_path):
    # 22F188 -> 62F187 is the expected Hyundai/Kia -1 identity offset.
    path = _write(
        tmp_path,
        """
        sessions:
          - date: "2026-07-19"
            captures:
              - ecu: "0x7A8"
                pid: "22F188"
                payload: "62F187414243"
                time: "10:00:00"
        """,
    )
    assert _capture_echo_warnings(path) == []


def test_did_offset_minus_two_flagged(tmp_path):
    # A -2 lag is a genuine stale frame, not the HK quirk.
    path = _write(
        tmp_path,
        """
        sessions:
          - date: "2026-07-19"
            captures:
              - ecu: "0x7A8"
                pid: "22F195"
                payload: "62F193414243"
                time: "10:00:00"
        """,
    )
    warnings = _capture_echo_warnings(path)
    assert len(warnings) == 1
    assert "F193" in warnings[0]


def test_missing_fields_skipped(tmp_path):
    path = _write(
        tmp_path,
        """
        sessions:
          - date: "2026-07-19"
            captures:
              - ecu: "0x7EB"
                pid: "2102"
                time: "16:13:04"
        """,
    )
    assert _capture_echo_warnings(path) == []


def test_nonhex_error_string_flagged(tmp_path):
    path = _write(
        tmp_path,
        """
        sessions:
          - date: "2026-07-19"
            captures:
              - ecu: "0x7AD"
                pid: "1001"
                payload: "NO DATA"
                time: "10:00:00"
        """,
    )
    warnings = _capture_nonhex_warnings(path)
    assert len(warnings) == 1
    assert "not hex" in warnings[0]
    assert "1001" in warnings[0]


def test_nonhex_mixed_ascii_flagged(tmp_path):
    path = _write(
        tmp_path,
        """
        sessions:
          - date: "2026-07-19"
            captures:
              - ecu: "0x778"
                pid: "F187"
                payload: "62F18791950G7510"
                time: "10:00:00"
        """,
    )
    warnings = _capture_nonhex_warnings(path)
    assert len(warnings) == 1
    assert "not hex" in warnings[0]


def test_nonhex_valid_payload_no_warning(tmp_path):
    path = _write(
        tmp_path,
        """
        sessions:
          - date: "2026-07-19"
            captures:
              - ecu: "0x7EB"
                pid: "2102"
                payload: "6102F8F8000001"
                time: "10:00:00"
        """,
    )
    assert _capture_nonhex_warnings(path) == []


def test_nonhex_missing_payload_skipped(tmp_path):
    path = _write(
        tmp_path,
        """
        sessions:
          - date: "2026-07-19"
            captures:
              - ecu: "0x7EB"
                pid: "2102"
                time: "10:00:00"
        """,
    )
    assert _capture_nonhex_warnings(path) == []
