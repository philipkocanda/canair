"""Deterministic regression tests for the blind-rediscovery harness (no LLM).

Covers the three mechanical guarantees the on-demand eval relies on:
* :func:`canlib.blind.strip_profile` removes every answer-bearing field yet leaves
  a profile that still validates and resolves captures;
* :func:`canlib.blind.grade_answer` scores exact / scale-equivalent / partial /
  miss / categorical guesses correctly;
* :func:`canlib.blind.select_targets` yields the curated corpus and a reproducible
  seeded-random draw.
"""

from __future__ import annotations

import pytest

from canlib import blind
from canlib.profile import resolve_profile


@pytest.fixture(scope="module")
def source_root():
    # conftest pins CANAIR_PROFILE=ioniq-2017 for the suite.
    return resolve_profile().root


@pytest.fixture(scope="module")
def sandbox(source_root, tmp_path_factory):
    dst = tmp_path_factory.mktemp("blind") / "prof"
    report = blind.strip_profile(source_root, dst, scrub_labels=True)
    return dst, report


# ── strip ─────────────────────────────────────────────────────────────────────
def test_strip_leaves_no_answer_content(sandbox):
    dst, report = sandbox
    assert report.residual_leaks == []
    assert report.params_removed > 0
    # No parameter definitions, notes, research, or when-predicates anywhere.
    for path in (dst / "ecus").glob("*.yaml"):
        text = path.read_text()
        assert "parameters:" not in text, path.name
        assert "\n  notes:" not in text and "\nnotes:" not in text, path.name
        assert "research:" not in text, path.name
        assert "expression:" not in text, path.name
    assert "when:" not in (dst / "vehicle_states.yaml").read_text()


def test_strip_keeps_addresses_and_draft_pids(sandbox):
    dst, _ = sandbox
    bms = (dst / "ecus" / "bms.yaml").read_text()
    assert "tx_id:" in bms  # address block retained → captures still resolve
    assert "pids:" in bms  # PID keys retained
    assert "status: draft" in bms  # active PIDs demoted to draft


def test_generated_and_reference_dirs_dropped(sandbox):
    dst, _ = sandbox
    assert not (dst / "out").exists()
    assert not (dst / "references").exists()
    assert not (dst / "signals").exists()


def test_stripped_profile_validates(sandbox):
    dst, _ = sandbox
    from canlib.cli import main

    # `validate all` returns 0 on a WARN-only profile (parameter-less draft PIDs).
    rc = main(["--profile", str(dst), "validate", "all"])
    assert rc == 0


def test_captures_still_resolve(source_root, sandbox):
    dst, _ = sandbox
    # MCU 2102 (rx 0x7EB) has ample captures; they must survive the strip.
    payloads = blind.load_target_payloads(dst, "0x7EB", "2102")
    assert len(payloads) > 50


# ── grade (synthetic, deterministic) ─────────────────────────────────────────────
def _payloads_16(pairs):
    """Build 16-byte WiCAN payloads with (hi, lo) at indices 10, 11."""
    return [bytes([0] * 10 + [hi, lo] + [0] * 4) for hi, lo in pairs]


NUMERIC_PAIRS = [
    (0, 10),
    (0, 50),
    (0, 200),
    (1, 0),
    (1, 100),
    (2, 0),
    (3, 250),
    (5, 5),
    (7, 42),
    (9, 9),
]


def test_grade_exact():
    payloads = _payloads_16(NUMERIC_PAIRS)
    g = blind.grade_answer("[S10:S11]", {"expression": "[S10:S11]"}, payloads)
    assert g["verdict"] == blind.EXACT


def test_grade_equivalent_up_to_scale():
    payloads = _payloads_16(NUMERIC_PAIRS)
    g = blind.grade_answer("[S10:S11]/10", {"expression": "[S10:S11]"}, payloads)
    assert g["verdict"] == blind.EQUIVALENT_SCALE
    assert abs(g["pearson"]) >= 0.999


def test_grade_miss_on_unrelated_byte():
    payloads = _payloads_16(NUMERIC_PAIRS)
    # B0 is constant 0 across all payloads → zero variance → not a match.
    g = blind.grade_answer("B0", {"expression": "[S10:S11]"}, payloads)
    assert g["verdict"] == blind.MISS


def test_grade_expr_error():
    payloads = _payloads_16(NUMERIC_PAIRS)
    g = blind.grade_answer("B999", {"expression": "[S10:S11]"}, payloads)  # index out of range
    assert g["verdict"] == blind.ERROR


def test_grade_insufficient_data():
    payloads = _payloads_16(NUMERIC_PAIRS[:3])
    g = blind.grade_answer("[S10:S11]", {"expression": "[S10:S11]"}, payloads, min_n=8)
    assert g["verdict"] == blind.INSUFFICIENT


def test_grade_categorical_match():
    # Enum truth on B5; guessing the same byte partitions identically → match.
    payloads = [bytes([0] * 5 + [code] + [0] * 4) for code in [0, 1, 2, 0, 1, 2, 3, 0, 1, 3]]
    truth = {"expression": "B5", "type": "enum", "values": {0: "off", 1: "a", 2: "b", 3: "c"}}
    g = blind.grade_answer("B5", truth, payloads)
    assert g["verdict"] == blind.CATEGORICAL_MATCH
    g2 = blind.grade_answer("B0", truth, payloads)  # constant → no association
    assert g2["verdict"] == blind.MISS


# ── select ──────────────────────────────────────────────────────────────────────
def test_select_curated(source_root):
    targets = blind.select_targets(source_root, curated=True)
    assert len(targets) >= 12
    names = {(t.ecu, t.pid, t.name) for t in targets}
    assert ("MCU", "2102", "MCU_MOTOR_RPM") in names
    # Every curated target carries its private ground truth + a blindfolded quest.
    for t in targets:
        assert t.expression and t.rx.startswith("0x")
        assert "expression" not in t.quest()


def test_select_random_is_reproducible(source_root):
    a = blind.select_targets(source_root, curated=False, n=8, seed=123)
    b = blind.select_targets(source_root, curated=False, n=8, seed=123)
    assert [(t.ecu, t.pid, t.name) for t in a] == [(t.ecu, t.pid, t.name) for t in b]
    assert 0 < len(a) <= 8
