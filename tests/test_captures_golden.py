"""Golden-output gate for the ``captures`` views' human text.

``tests/test_captures.py`` covers the ``--json`` shapes and behaviors of every
mode, but nothing pins the **human** rendering: column alignment, the ``… +N more
not shown`` truncation footer, the unmatched-selector notice, the per-view
layouts. That text is emitted from a handful of print-heavy functions
(``cmd_list``/``cmd_latest``/``cmd_summary``/``cmd_sessions``/``cmd_diff``), and
the failure mode of moving them between modules is a silent whitespace or
ordering shift no assertion sees. So this module pins each view's full stdout
against a committed golden file — the safety net for the
``commands/captures/`` package split
(``plans/2026-08-05-captures-command-package-split.md``).

**Why not in :mod:`tests.test_analysis_golden`?** That module is specifically a
*byte-label* gate — its ``test_goldens_contain_byte_labels`` would reject a
``--summary``/``--sessions`` golden, which legitimately carries no ``Bnn``. The
shared harness lives in :mod:`tests._golden`; only the case lists differ.

**No real capture labels/notes in the goldens.** ``cmd_sessions`` and the default
list view render free-text session/capture ``label``/``notes``, which is exactly
the PII surface the screenshot policy keeps out of shareable artifacts. Those
views are therefore pinned **only** against the synthetic fixture profile;
``ioniq-2017`` is used only for views that render no free text (enforced by
:meth:`TestGoldenHarnessItself.test_free_text_views_use_a_fixture_profile`).

Regenerate after an *intended* change, then **read the diff**::

    CANAIR_REGEN_GOLDEN=1 uv run pytest tests/test_captures_golden.py -q
    git diff tests/fixtures/golden/
"""

from __future__ import annotations

import pytest

from tests._golden import FIXTURE_PROFILES_DIR, SCOPE_FLAGS, check_golden, run_cli

# Synthetic two-ECU profile with 24 timed sessions across two dated files. Frozen
# test data no recording session appends to, and its labels/states are invented —
# so it is the only profile the free-text-rendering views may be pinned against.
FIXTURE_PROFILE = str(FIXTURE_PROFILES_DIR / "single-frame")

# Modes that select a view other than the default capture list.
_VIEW_FLAGS = frozenset({"--summary", "--sessions", "--latest", "--diff", "--step"})

# ...of those, the ones that render free-text session/capture label+notes. The
# default list view (no view flag at all) does too, via `_print_entry`.
_FREE_TEXT_VIEW_FLAGS = frozenset({"--sessions"})

# (name, profile, argv). Coverage rationale — one case per rendering path:
#   summary            the aggregate count tables (by ECU, by date)
#   sessions           the session table-of-contents block
#   list-truncated     the default list view + the --limit truncation footer
#   list-cross-ecu     the same view with the ECU column (>1 ECU in the results)
#   list-unmatched     the "no captures matched selector" + available-ECUs notice
#   latest             the dedup-per-PID view
#   diff               the byte-diff block (single-frame payload)
#   diff-multiframe    the byte-diff block with a multi-frame payload + --rulers,
#                      against real PID definitions (many params, verified marks)
#   can                the domain-B log list, empty path
CASES: list[tuple[str, str, list[str]]] = [
    ("captures-summary", FIXTURE_PROFILE, ["captures", "uds", "--summary"]),
    (
        "captures-sessions",
        FIXTURE_PROFILE,
        ["captures", "uds", "--sessions", "--last-sessions", "3"],
    ),
    (
        "captures-list-truncated",
        FIXTURE_PROFILE,
        ["captures", "uds", "ALPHA", "22F001", "--limit", "4"],
    ),
    (
        "captures-list-cross-ecu",
        FIXTURE_PROFILE,
        ["captures", "uds", "ALPHA:22F001 BETA:22F002", "--limit", "3"],
    ),
    (
        "captures-list-unmatched",
        FIXTURE_PROFILE,
        ["captures", "uds", "ALPHA:22F001 GAMMA:22F009", "--limit", "2"],
    ),
    ("captures-latest", FIXTURE_PROFILE, ["captures", "uds", "--latest"]),
    (
        "captures-diff",
        FIXTURE_PROFILE,
        ["captures", "uds", "ALPHA", "22F001", "--diff", "--last-sessions", "2"],
    ),
    (
        "captures-diff-multiframe",
        "ioniq-2017",
        ["captures", "uds", "BMS", "2101", "--diff", "--date", "2026-07-21", "--rulers"],
    ),
    ("captures-can", FIXTURE_PROFILE, ["captures", "can"]),
]


def _renders_free_text(argv: list[str]) -> bool:
    """Whether ``argv`` selects a view that prints session/capture label+notes."""
    flags = set(argv)
    if flags & _FREE_TEXT_VIEW_FLAGS:
        return True
    # No view flag at all == the default capture list view, which prints both.
    return not (flags & _VIEW_FLAGS)


@pytest.mark.parametrize("name,profile,argv", CASES, ids=[c[0] for c in CASES])
def test_captures_view_output_is_unchanged(name, profile, argv, capsys):
    check_golden(
        name,
        run_cli(profile, argv, capsys),
        hint="this is the text users read from every captures view.",
    )


class TestGoldenHarnessItself:
    """The gate is worthless if it can't actually fail."""

    def test_cases_cannot_drift_as_captures_grow(self):
        """Every case must be immune to a new ``--save`` landing in the profile.

        Same rule as :mod:`tests.test_analysis_golden`: a case is safe if it runs
        against a frozen fixture profile or is pinned to a closed date range.
        ``captures`` has no volume-independent mode — every view here reports
        counts, timestamps or payload lists.
        """
        drifting = [
            name
            for name, profile, argv in CASES
            if not (profile.startswith(str(FIXTURE_PROFILES_DIR)) or (set(argv) & SCOPE_FLAGS))
        ]
        assert not drifting, (
            "these golden cases will drift when new captures are recorded — scope "
            f"them with --date/--until or use the fixture profile: {drifting}"
        )

    def test_free_text_views_use_a_fixture_profile(self):
        """No real capture label/note may be committed into a golden file.

        ``--sessions`` and the default list view print free-text session/capture
        metadata. Pinning those against a *real* profile would copy a car owner's
        capture labels and notes into a shareable test fixture — the same leak the
        screenshot policy forbids. Real profiles are allowed only for the views
        that render no free text (``--summary``/``--latest``/``--diff``).
        """
        leaking = [
            name
            for name, profile, argv in CASES
            if _renders_free_text(argv) and not profile.startswith(str(FIXTURE_PROFILES_DIR))
        ]
        assert not leaking, (
            "these cases would bake real capture labels/notes into a golden — "
            f"point them at the synthetic fixture profile: {leaking}"
        )

    def test_runs_are_deterministic(self, capsys):
        """Two runs of the same case must agree, or goldens are useless."""
        name, profile, argv = CASES[0]
        assert run_cli(profile, argv, capsys) == run_cli(profile, argv, capsys), (
            f"{name} is nondeterministic"
        )
