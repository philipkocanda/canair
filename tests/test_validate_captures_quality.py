"""Tests for the capture data-quality lint (dropped/stale ISO-TP frames).

A session's recorded ``quality`` footprint carries ``drop``/``stale`` counts —
the ISO-TP reassembly faults that silently corrupted multi-frame payloads before
the transport hardening. ``_capture_quality_warnings`` flags such sessions as a
soft warning (never an error); non-answer categories (no_data/bus/decode) don't
warn since nothing was stored.
"""

import json
import textwrap

import yaml

from canlib.commands.validate import _capture_quality_warnings


def _write(tmp_path, body: str):
    p = tmp_path / "2026-07-28.json"
    p.write_text(json.dumps(yaml.safe_load(textwrap.dedent(body))))
    return p


def test_flags_dropped_frames(tmp_path):
    path = _write(
        tmp_path,
        """
        sessions:
          - date: "2026-07-28"
            label: "run"
            transport: "wican-ws"
            quality: {exchanges: 100, drop: 2}
            captures:
              - {ecu: "0x7EC", pid: "2101", payload: "6101AA", time: "10:00:00"}
        """,
    )
    warnings = _capture_quality_warnings(path)
    assert len(warnings) == 1
    assert "2 dropped/stale" in str(warnings[0])
    assert "wican-ws" in str(warnings[0])


def test_stale_counts_too(tmp_path):
    path = _write(
        tmp_path,
        """
        sessions:
          - date: "2026-07-28"
            label: "run"
            quality: {exchanges: 50, stale: 1, drop: 1}
            captures:
              - {ecu: "0x7EC", pid: "2101", payload: "6101AA", time: "10:00:00"}
        """,
    )
    warnings = _capture_quality_warnings(path)
    assert len(warnings) == 1
    assert "2 dropped/stale" in str(warnings[0])


def test_clean_session_no_warning(tmp_path):
    path = _write(
        tmp_path,
        """
        sessions:
          - date: "2026-07-28"
            label: "run"
            transport: "slcan-tcp"
            quality: {exchanges: 100}
            captures:
              - {ecu: "0x7EC", pid: "2101", payload: "6101AA", time: "10:00:00"}
        """,
    )
    assert _capture_quality_warnings(path) == []


def test_non_drop_errors_do_not_warn(tmp_path):
    # Timeouts/bus/decode are non-answers — nothing corrupt was stored.
    path = _write(
        tmp_path,
        """
        sessions:
          - date: "2026-07-28"
            label: "run"
            quality: {exchanges: 100, no_data: 5, bus: 1, decode: 2}
            captures:
              - {ecu: "0x7EC", pid: "2101", payload: "6101AA", time: "10:00:00"}
        """,
    )
    assert _capture_quality_warnings(path) == []


def test_no_quality_field_skipped(tmp_path):
    path = _write(
        tmp_path,
        """
        sessions:
          - date: "2026-07-28"
            label: "legacy"
            captures:
              - {ecu: "0x7EC", pid: "2101", payload: "6101AA", time: "10:00:00"}
        """,
    )
    assert _capture_quality_warnings(path) == []
