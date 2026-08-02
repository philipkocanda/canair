"""Tests for canlib.unit_guess — make-neutral built-ins + profile extension."""

from __future__ import annotations

from canlib.unit_guess import DEFAULT_UNIT_CANDIDATES, resolve_unit_candidates


def test_builtins_are_make_neutral():
    for _factor, _offset, label, hint, _dim in DEFAULT_UNIT_CANDIDATES:
        assert "HK" not in label
        assert "HK" not in (hint or "")


def test_resolve_none_returns_builtins():
    assert resolve_unit_candidates(None) == DEFAULT_UNIT_CANDIDATES
    assert resolve_unit_candidates({}) == DEFAULT_UNIT_CANDIDATES


def test_resolve_appends_profile_candidates():
    got = resolve_unit_candidates(
        {"unit_guess_candidates": [{"factor": 0.25, "offset": -50, "label": "raw/4−50"}]}
    )
    assert len(got) == len(DEFAULT_UNIT_CANDIDATES) + 1
    assert got[-1] == (0.25, -50.0, "raw/4−50", None, None)


def test_resolve_keeps_hint_and_dimension():
    got = resolve_unit_candidates(
        {
            "unit_guess_candidates": [
                {"factor": 0.1, "label": "raw/10", "hint": "pack A", "dimension": "current"}
            ]
        }
    )
    assert got[-1] == (0.1, 0.0, "raw/10", "pack A", "current")


def test_resolve_drops_malformed_entries():
    got = resolve_unit_candidates(
        {
            "unit_guess_candidates": [
                "not-a-mapping",
                {"label": "no-factor"},
                {"factor": True, "label": "bool-factor"},
                {"factor": 2.0, "label": ""},
                {"factor": 2.0, "label": "ok"},
            ]
        }
    )
    assert len(got) == len(DEFAULT_UNIT_CANDIDATES) + 1
    assert got[-1] == (2.0, 0.0, "ok", None, None)
