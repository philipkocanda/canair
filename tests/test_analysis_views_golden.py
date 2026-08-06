"""Golden-output gate for the analysis verbs' **default views**.

:mod:`tests.test_analysis_golden` pins the *byte-label* paths (``--dump-bytes``,
``--discriminate``, ``--find-mirrors``, ``--bits``) because a silent off-by-PCI is
invisible to free-running assertions. That leaves the views users actually reach
for first — ``decode``'s value-range table, ``--compact``, ``--stats``, and
``correlate``'s ranked/against/overlap blocks — pinned by **nothing**. Each is
emitted from a print-heavy renderer, and the failure mode of moving those between
modules is a whitespace, ordering or section-separator shift no assertion sees.

So this module pins their full stdout: the safety net for the
``commands/decode/`` / ``commands/correlate/`` / ``commands/investigate/`` package
splits (``plans/2026-08-06-command-packages-and-live-split.md``).

**Why not in :mod:`tests.test_analysis_golden`?** That module's
``test_goldens_contain_byte_labels`` gate would reject a ``--stats``/``--compact``
golden, which legitimately carries no ``Bnn`` — and its docstring scopes it to
byte-label emission. Bolting these on would corrupt its stated purpose. The shared
harness lives in :mod:`tests._golden`; only the case lists differ.

**No free text in these goldens.** None of the views pinned here renders a session
``label`` or capture ``notes`` (``--compact`` prints only the ``[STATE]`` divider,
which is controlled vocabulary), so the PII constraint that forces
:mod:`tests.test_captures_golden` onto a synthetic profile does not bind. The one
``ioniq-2017`` case is a value-range table of parameter names. Keep it that way: a
new case that renders free text belongs on the fixture profile.

Regenerate after an *intended* change, then **read the diff**::

    CANAIR_REGEN_GOLDEN=1 uv run pytest tests/test_analysis_views_golden.py -q
    git diff tests/fixtures/golden/
"""

from __future__ import annotations

import pytest

from tests._golden import FIXTURE_PROFILES_DIR, SCOPE_FLAGS, check_golden, run_cli

# Synthetic two-ECU profile: ALPHA:22F001 (a ramp + a toggling flag bit) and
# BETA:22F002 (a co-polled ramp), 24 timed captures each. Frozen — no recording
# session appends to it — so a case against it can never drift.
SINGLE_FRAME_PROFILE = str(FIXTURE_PROFILES_DIR / "single-frame")

# Closed upper bound for the bundled-profile case, matching
# tests/test_analysis_golden.py::FROZEN_UNTIL. `captures/` is append-only, so an
# unscoped query against a real profile pins a moving target.
FROZEN_UNTIL = "2026-08-02"

# (name, profile, argv). One case per renderer the package splits will move:
#   decode-value-ranges        print_value_ranges — the default view, the one a
#                              bare `canair decode ECU PID` lands on
#   decode-value-ranges-multi  the same, two PIDs: pins the per-PID section split
#   decode-value-ranges-real   the same against real definitions: the `scope:`
#                              line and the `(constant)` annotation, neither of
#                              which the fixture profile can produce
#   decode-compact             the aligned one-row-per-capture renderer
#   decode-stats               the n/distinct/mean/median/stdev block
#   decode-stats-by-state      the --group-by state variant (per-segment headers)
#   correlate-params           the ranked cross-signal list over params only
#                              (test_analysis_golden pins it only with --bytes)
#   correlate-against          the "vs one reference" ranking
#   correlate-overlap          the co-poll overlap report
#   investigate-default-table  investigate's default byte table WITH rows — the
#                              existing `investigate-single-frame` golden is three
#                              lines of "no varying bytes", so it pins no table
CASES: list[tuple[str, str, list[str]]] = [
    ("decode-value-ranges", SINGLE_FRAME_PROFILE, ["decode", "ALPHA", "22F001"]),
    (
        "decode-value-ranges-multi",
        SINGLE_FRAME_PROFILE,
        ["decode", "ALPHA:22F001 BETA:22F002"],
    ),
    (
        # IGPM 22BC02 on purpose: the bundled profile's stable all-constant
        # single-frame PID, already the reference target of five goldens in
        # test_analysis_golden. A PID with live undecoded bitfields (22BC03) would
        # churn this golden every time someone works the bitfield backlog, for no
        # extra coverage — `(constant)` and `scope:` are all this case is here for.
        "decode-value-ranges-real",
        "ioniq-2017",
        ["decode", "IGPM", "22BC02", "--until", FROZEN_UNTIL],
    ),
    ("decode-compact", SINGLE_FRAME_PROFILE, ["decode", "ALPHA", "22F001", "--compact"]),
    ("decode-stats", SINGLE_FRAME_PROFILE, ["decode", "ALPHA", "22F001", "--stats"]),
    (
        "decode-stats-by-state",
        SINGLE_FRAME_PROFILE,
        ["decode", "ALPHA", "22F001", "--stats", "--group-by", "state"],
    ),
    ("correlate-params", SINGLE_FRAME_PROFILE, ["correlate", "uds"]),
    (
        "correlate-against",
        SINGLE_FRAME_PROFILE,
        ["correlate", "uds", "--against", "ALPHA:22F001:ALPHA_RAMP"],
    ),
    ("correlate-overlap", SINGLE_FRAME_PROFILE, ["correlate", "uds", "--overlap"]),
    (
        "investigate-default-table",
        SINGLE_FRAME_PROFILE,
        ["investigate", "uds", "ALPHA", "22F001"],
    ),
]

# `decode --compact --changes-only` is deliberately absent: it shares the compact
# renderer and, on both the fixture profile and the bundled one, collapses nothing
# (every row differs from its predecessor), so its output is byte-identical to
# `--compact`. A golden for it would pin a duplicate and imply coverage it has not
# got. Its row-dropping logic is unit-tested, not goldened.


@pytest.fixture(autouse=True)
def _deterministic_grid_region(tmp_path, monkeypatch):
    """Pin ``grid_region`` so the physical scan's one-shot prompt never fires.

    Same reason as :mod:`tests.test_analysis_golden`: ``investigate`` runs a
    physical-band scan which, when ``grid_region`` is unset, emits a *one-time*
    "no grid_region set" note and records a sentinel in the user config — making
    output depend on which test scanned first.
    """
    from canlib import config

    cfg = tmp_path / "canair"
    cfg.mkdir(parents=True, exist_ok=True)
    (cfg / "config.yaml").write_text("grid_region: EU\n")
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    config.load_config.cache_clear()
    yield
    config.load_config.cache_clear()


@pytest.mark.parametrize("name,profile,argv", CASES, ids=[c[0] for c in CASES])
def test_analysis_view_output_is_unchanged(name, profile, argv, capsys):
    check_golden(
        name,
        run_cli(profile, argv, capsys),
        hint="this is the text users read from the default analysis views.",
    )


class TestGoldenHarnessItself:
    """The gate is worthless if it can't actually fail."""

    def test_cases_cannot_drift_as_captures_grow(self):
        """Every case must be immune to a new ``--save`` landing in the profile.

        ``captures/`` is append-only, so a case querying a volume-dependent verb
        over an unbounded range breaks on the next recording. Every view here
        reports a capture count, so none is volume-independent: each case must be
        date-scoped or run against the frozen fixture profile.
        """
        drifting = [
            name
            for name, profile, argv in CASES
            if not (profile.startswith(str(FIXTURE_PROFILES_DIR)) or (set(argv) & SCOPE_FLAGS))
        ]
        assert not drifting, (
            "these golden cases will drift when new captures are recorded — add "
            f"--until FROZEN_UNTIL, or point them at the fixture profile: {drifting}"
        )

    def test_cases_pin_a_view_not_a_byte_label_path(self):
        """Keep this module's scope distinct from ``test_analysis_golden``.

        That module owns the byte-label paths and gates on labels being present.
        A case added here with one of its flags belongs *there* instead — and
        would be pinned twice, so the two goldens could silently disagree.
        """
        byte_label_flags = {"--dump-bytes", "--discriminate", "--find-mirrors", "--bits"}
        misplaced = [name for name, _profile, argv in CASES if set(argv) & byte_label_flags]
        assert not misplaced, (
            "these belong in tests/test_analysis_golden.py, which gates on byte "
            f"labels: {misplaced}"
        )

    def test_runs_are_deterministic(self, capsys):
        """Two runs of the same case must agree, or goldens are useless."""
        name, profile, argv = CASES[0]
        assert run_cli(profile, argv, capsys) == run_cli(profile, argv, capsys), (
            f"{name} is nondeterministic"
        )
