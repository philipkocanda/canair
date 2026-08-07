"""Tests for the untimed-payload ratchet (`validate captures --max-untimed N`).

Writes are enforced to carry a timestamp, so a profile's untimed-payload count
only ever falls. The ratchet turns the un-satisfiable "must be zero" (the
grandfathered legacy rows can never meet it) into an enforceable "must not GROW":
a real CI gate that needs no history rewrite. See
``plans/2026-08-07-analysis-tooling-followups.md`` Part B.
"""

from __future__ import annotations

import json

import pytest

from canlib import profile
from canlib.commands.validate import _run_captures
from canlib.pids import clear_cache

# The ioniq-2017 baseline gated in .github/workflows/ci.yml. The count can only
# fall (writes enforce a timestamp), so the profile must sit AT or under it — a
# real regression pushes it over and fails both CI and this test in lockstep.
IONIQ_2017_UNTIMED_BASELINE = 284


@pytest.fixture(autouse=True)
def _restore_active_profile():
    saved = profile._active
    clear_cache()
    yield
    profile._active = saved
    clear_cache()


def _mk_profile(tmp_path, *, n_untimed: int) -> None:
    root = tmp_path / "prof"
    (root / "ecus").mkdir(parents=True)
    (root / "captures").mkdir()
    (root / "profile.yaml").write_text('car_model: "T"\ninit: "ATSP6;"\n')
    (root / "ecus" / "eng.yaml").write_text(
        "ENG:\n  tx_id: 0x7E4\n  identity:\n    id_protocol: UDS\n"
    )
    # A session of payload captures with NO `time` — the untimed rows the ratchet
    # counts. rx 0x7EC = tx 0x7E4 + 0x08 so the address resolves cleanly.
    caps = [{"rx": "0x7EC", "pid": "2101", "payload": "6101AA"} for _ in range(n_untimed)]
    (root / "captures" / "2026-04-16.json").write_text(
        json.dumps({"sessions": [{"date": "2026-04-16", "label": "t", "captures": caps}]})
    )
    profile.set_active(str(root))
    clear_cache()


def test_over_baseline_fails(tmp_path, capsys):
    _mk_profile(tmp_path, n_untimed=5)
    assert _run_captures(max_untimed=4) == 1
    assert "exceeds the --max-untimed 4 baseline" in capsys.readouterr().out


def test_at_baseline_passes(tmp_path, capsys):
    _mk_profile(tmp_path, n_untimed=5)
    assert _run_captures(max_untimed=5) == 0
    assert "within the --max-untimed 5 baseline" in capsys.readouterr().out


def test_under_baseline_passes(tmp_path):
    _mk_profile(tmp_path, n_untimed=3)
    assert _run_captures(max_untimed=5) == 0


def test_no_ratchet_is_warnings_only(tmp_path, capsys):
    """Default (no --max-untimed): untimed rows warn but never fail — the pre-ratchet behavior."""
    _mk_profile(tmp_path, n_untimed=5)
    assert _run_captures() == 0
    assert "untimed payload capture(s)" in capsys.readouterr().out


def test_bundled_ioniq_2017_sits_at_its_ci_baseline(capsys):
    """Guard: the real profile must not exceed the baseline CI gates it at.

    Relies on the suite-wide ``CANAIR_PROFILE=ioniq-2017`` pin (conftest).
    """
    assert _run_captures(max_untimed=IONIQ_2017_UNTIMED_BASELINE) == 0


def _mk_profile_with_quality_warning(tmp_path, *, n_untimed: int) -> None:
    """Untimed payload rows PLUS one session flagged for dropped ISO-TP frames."""
    root = tmp_path / "prof"
    (root / "ecus").mkdir(parents=True)
    (root / "captures").mkdir()
    (root / "profile.yaml").write_text('car_model: "T"\ninit: "ATSP6;"\n')
    (root / "ecus" / "eng.yaml").write_text(
        "ENG:\n  tx_id: 0x7E4\n  identity:\n    id_protocol: UDS\n"
    )
    untimed = [{"rx": "0x7EC", "pid": "2101", "payload": "6101AA"} for _ in range(n_untimed)]
    (root / "captures" / "2026-04-16.json").write_text(
        json.dumps(
            {
                "sessions": [
                    {"date": "2026-04-16", "label": "untimed", "captures": untimed},
                    {
                        "date": "2026-04-16",
                        "label": "dropped",
                        "quality": {"exchanges": 10, "drop": 2},
                        "captures": [
                            {"rx": "0x7EC", "pid": "2101", "payload": "6101AA", "time": "09:00:00"}
                        ],
                    },
                ]
            }
        )
    )
    profile.set_active(str(root))
    clear_cache()


def test_untimed_collapsed_to_a_count_by_default(tmp_path, capsys):
    """The untimed class is a footer COUNT, not a per-file stream that drowns the rest."""
    _mk_profile_with_quality_warning(tmp_path, n_untimed=5)
    assert _run_captures() == 0
    out = capsys.readouterr().out
    # The real (quality) warning surfaces; the 5 untimed rows do NOT each print.
    assert "dropped/stale" in out
    assert "1 warning(s)" in out, "untimed rows must not inflate the warning count"
    assert "5 untimed payload capture(s)" in out  # the collapsed footer count


def test_show_untimed_restores_the_per_file_detail(tmp_path, capsys):
    _mk_profile_with_quality_warning(tmp_path, n_untimed=5)
    assert _run_captures(show_untimed=True) == 0
    out = capsys.readouterr().out
    assert "6 warning(s)" in out  # 5 untimed + 1 quality
    assert "no usable time" in out
