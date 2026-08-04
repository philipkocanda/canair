"""Golden-output gate for the analysis verbs' byte labels.

The ISO-TP-canonical re-homing (Phase 2b of
``plans/2026-07-24-byte-notation-phase2-isotp-canonical.md``) changes the analysis
engine's *internal* byte model: series stop being keyed by a synthesized WiCAN
expression string and start carrying an explicit byte reference. The plan's
non-negotiable constraint is that **default (``wican``) rendering stays
byte-identical**, because those labels are what every user reads and what
``--promote`` persists.

Free-running assertions can't prove that: the labels are emitted from a dozen
render paths and the failure mode is a silent off-by-PCI. So this module pins the
full stdout of each label-emitting command against a committed golden file.

**Stability.** Cases are scoped to *fixed historical dates* in the bundled
``ioniq-2017`` profile. Captures are an append-only log, so a past day's data is
frozen — new recordings can't drift these goldens, unlike an unscoped query.

**ANSI is stripped** so the goldens stay reviewable in a diff (a colour change is
out of scope here and is covered by the screenshot check). Labels are what matter.

Regenerate after an *intended* change, then **read the diff**::

    CANAIR_REGEN_GOLDEN=1 uv run pytest tests/test_analysis_golden.py -q
    git diff tests/fixtures/golden/
"""

from __future__ import annotations

import os
import re
from pathlib import Path
from typing import ClassVar

import pytest

from canlib import cli

GOLDEN_DIR = Path(__file__).parent / "fixtures" / "golden"
REGEN = os.environ.get("CANAIR_REGEN_GOLDEN") == "1"

_ANSI_RE = re.compile(r"\x1b\[[0-9;]*[A-Za-z]")

# (name, argv). Every case must emit byte labels (Bnn / Bnn:k) or byte-derived
# columns, and must be scoped to frozen dates so the output can't drift.
#
# Coverage rationale:
#   coverage-*        UNMAPPED / BITS label emission (both frame layouts)
#   investigate-*     per-byte + per-bit ranked table, incl. the bit path
#   decode-mirrors    byte- and bit-level mirror labels
#   decode-discrim    raw-byte + bit discriminability labels
#   decode-dump       the byte-offset matrix column headers (Bnn)
#   correlate-*       cross-signal labels, incl. the 4-field bit form
#   hunt-*            the swept-byte hit labels + promoted expression form
CASES: list[tuple[str, list[str]]] = [
    # -- single-frame PID (ONE PCI byte): the layout that broke physical_scan --
    ("coverage-single-frame", ["coverage", "IGPM", "22BC02"]),
    ("investigate-single-frame", ["investigate", "uds", "IGPM", "22BC02"]),
    # -- multi-frame PID (two FF PCI bytes + a CF PCI byte every 8) --
    ("coverage-multi-frame", ["coverage", "BMS", "2101"]),
    ("coverage-bitfields", ["coverage", "IGPM", "--bitfields"]),
    ("investigate-bits", ["investigate", "uds", "IGPM", "22BC03", "--bits"]),
    # -- decode: mirrors / discriminate / dump-bytes --
    ("decode-mirrors-bits", ["decode", "IGPM", "22BC03", "--find-mirrors", "--bits"]),
    (
        "decode-discriminate-bytes",
        ["decode", "IGPM", "22BC03", "--discriminate", "state", "--bytes"],
    ),
    ("decode-dump-bytes", ["decode", "IGPM", "22BC02", "--dump-bytes", "--date", "2026-07-22"]),
    (
        "decode-dump-bytes-signed",
        ["decode", "BMS", "2101", "--dump-bytes", "--signed", "--date", "2026-07-21"],
    ),
    # -- correlate: the 4-field bit label form lives here --
    ("correlate-bytes", ["correlate", "uds", "IGPM", "--bytes", "--until", "2026-08-02"]),
    ("correlate-bits", ["correlate", "uds", "IGPM", "--bits", "--until", "2026-08-02"]),
    # -- hunt: swept byte/interpretation labels --
    (
        "hunt-against",
        [
            "hunt",
            "uds",
            "AAF",
            "2181",
            "--against",
            "ESC:22C101:REAL_SPEED_KMH",
            "--until",
            "2026-08-02",
        ],
    ),
    # -- the same data rendered in every notation: proves the views stay in step --
    ("notation-isotp", ["coverage", "IGPM", "22BC02", "--notation", "isotp"]),
    ("notation-torque", ["coverage", "IGPM", "22BC02", "--notation", "torque"]),
    ("notation-bix", ["coverage", "IGPM", "22BC02", "--notation", "bix"]),
]


@pytest.fixture(autouse=True)
def _deterministic_grid_region(tmp_path, monkeypatch):
    """Pin ``grid_region`` so the physical scan's one-shot prompt never fires.

    ``investigate`` runs a physical-band scan, which (when ``grid_region`` is
    unset) emits a *one-time* "no grid_region set" note and records a
    ``grid_region_prompted`` sentinel in the user config. That makes the output
    order-dependent: whichever test scans first sees the note and the rest don't.
    Pinning the region makes every case deterministic in isolation *and* inside a
    full-suite run, and keeps the golden free of a message that is really about
    first-run onboarding rather than byte labels.
    """
    from canlib import config

    cfg = tmp_path / "canair"
    cfg.mkdir(parents=True, exist_ok=True)
    (cfg / "config.yaml").write_text("grid_region: EU\n")
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    config.load_config.cache_clear()
    yield
    config.load_config.cache_clear()


def _norm(text: str) -> str:
    """Strip ANSI and normalise line endings.

    ``--dump-bytes`` writes CSV through :mod:`csv`, which emits ``\\r\\n``, while
    ``Path.read_text`` universal-newlines it back to ``\\n`` — so an unnormalised
    comparison fails on a byte-identical run. Normalising also makes the goldens
    immune to git's autocrlf.
    """
    return _ANSI_RE.sub("", text).replace("\r\n", "\n")


def _run(argv: list[str], capsys) -> str:
    """Run one canair command, returning its normalised stdout+stderr."""
    try:
        cli.main(["--profile", "ioniq-2017", *argv])
    except SystemExit:
        pass  # argparse/verb exit codes are not what we're pinning
    cap = capsys.readouterr()
    return _norm(cap.out + cap.err)


@pytest.mark.parametrize("name,argv", CASES, ids=[c[0] for c in CASES])
def test_analysis_output_is_unchanged(name, argv, capsys):
    got = _run(argv, capsys)
    path = GOLDEN_DIR / f"{name}.txt"

    if REGEN:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(got)
        pytest.skip(f"regenerated {path.name}")

    assert got.strip(), f"{name} produced no output — it would pin nothing"
    assert path.exists(), (
        f"missing golden {path.name} — regenerate with CANAIR_REGEN_GOLDEN=1 and review the diff"
    )
    want = _norm(path.read_text())
    assert got == want, (
        f"{name}: analysis output drifted from its golden.\n"
        "If the change is intended, regenerate with CANAIR_REGEN_GOLDEN=1 "
        "and READ the diff — these labels are what --promote persists."
    )


class TestGoldenHarnessItself:
    """The gate is worthless if it can't actually fail."""

    # Cases whose output legitimately carries no byte label. `IGPM 22BC02` is the
    # bundled profile's ONLY single-frame PID with capture volume (275), and every
    # one of its data bytes is constant — so `investigate` has nothing to rank.
    #
    # That is itself the finding: the single-frame layout (one PCI byte) has
    # almost no real-data coverage in this profile, which is exactly how the
    # `physical_scan` and `--notation` off-by-one bugs survived. Deterministic
    # label coverage for a *varying* single-frame payload needs a synthetic
    # fixture profile — see tests/fixtures/profiles/.
    NO_LABEL_EXPECTED: ClassVar[set[str]] = {"investigate-single-frame"}

    def test_goldens_contain_byte_labels(self):
        """A golden with no byte reference can't detect a label regression."""
        if REGEN:
            pytest.skip("regenerating")
        # Bnn (WiCAN), Snn (--signed), iN (ISO-TP), Torque letters / bix columns.
        label_re = re.compile(r"\b[BS]\d{1,2}\b|\bi\d{1,2}\b|\bbix\b|Torque|UNMAPPED")
        missing = [
            name
            for name, _argv in CASES
            if name not in self.NO_LABEL_EXPECTED
            and (GOLDEN_DIR / f"{name}.txt").exists()
            and not label_re.search((GOLDEN_DIR / f"{name}.txt").read_text())
        ]
        assert not missing, f"goldens with no byte labels to pin: {missing}"

    def test_runs_are_deterministic(self, capsys):
        """Two runs of the same case must agree, or goldens are useless."""
        name, argv = CASES[0]
        assert _run(argv, capsys) == _run(argv, capsys), f"{name} is nondeterministic"
