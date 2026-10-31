"""Detecting an ECU's revision from its own answers (fingerprints), and remembering it.

Driven by the synthetic fixture ``tests/fixtures/profiles/variants/`` (see
``tests/test_ecu_revisions.py``): the 60 kWh car may have either VCU, whose ``kwp``
revision answers ``1A90`` and whose ``uds`` revision answers ``22F187``.

Plan: ``plans/2026-10-30-profile-variants-8-detection.md``.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import shutil
from pathlib import Path

import pytest
import yaml

from canlib import config
from canlib.pids import load_pids, revision_resolutions
from canlib.profile import ProfileError, set_active
from canlib.variants import detected as cache
from canlib.variants import fingerprints as fp

FIXTURE = Path(__file__).parent / "fixtures" / "profiles" / "variants"
VCU = yaml.safe_load((FIXTURE / "ecus" / "vcu.yaml").read_text())["VCU"]


@pytest.fixture(autouse=True)
def _fresh(monkeypatch, tmp_path):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "cfg"))
    monkeypatch.delenv("CANAIR_VARIANT", raising=False)
    config.load_config.cache_clear()
    yield
    config.load_config.cache_clear()


class FakeTerminal:
    """Answers the requests in ``answers`` positively and everything else with silence."""

    def __init__(self, answers=()):
        self.answers = set(answers)
        self.sent: list[tuple[int | None, str]] = []
        self._tx = None

    @contextlib.asynccontextmanager
    async def transaction(self):
        yield

    async def set_header(self, tx_id):
        self._tx = tx_id

    async def send_uds(self, request, **_kw):
        self.sent.append((self._tx, request))
        return {"ok": request in self.answers, "nrc": None}


class _Args:
    def __init__(self, *steps):
        self.multi = list(steps)


def _detect(terminal, *steps):
    from canlib.modes.revision_probe import detect_revisions, targeted_ecus

    return asyncio.run(detect_revisions(terminal, targeted_ecus(_Args(*steps))))


class TestMatching:
    def test_responds(self):
        assert fp.match(VCU, responded={"1A90": True}) == ["kwp"]
        assert fp.match(VCU, responded={"22F187": True, "1A90": False}) == ["uds"]
        assert fp.match(VCU, responded={"1A90": False, "22F187": False}) == []

    def test_a_request_not_sent_is_no_evidence(self):
        assert fp.match(VCU, responded={}) == []

    def test_identity_globs(self):
        ecu = {"revisions": {"a": {"fingerprints": [{"identity": {"part_number": "36601-0E1*"}}]},
                             "b": {"fingerprints": [{"identity": {"part_number": "36601-0E2*",
                                                                  "sw_id": "X?"}}]}}}  # fmt: skip
        assert fp.match(ecu, identity={"part_number": " 36601-0E100 "}) == ["a"]
        assert fp.match(ecu, identity={"part_number": "36601-0E200"}) == []  # sw_id missing
        assert fp.match(ecu, identity={"part_number": "36601-0E200", "sw_id": "X1"}) == ["b"]

    def test_several_matches_settle_nothing(self):
        assert fp.detected(["kwp", "uds"]) is None
        assert fp.detected(["kwp"]) == "kwp"

    def test_probes_are_one_per_revision_cheapest_first(self):
        assert fp.probes(VCU, ["kwp", "uds"]) == ["1A90", "22F187"]
        assert fp.probes(VCU, ["uds"]) == ["22F187"]


class TestResolution:
    def test_a_remembered_detection_resolves_an_ambiguous_ecu(self):
        cache.remember("variants", "60kwh", "VCU", "uds")
        set_active(str(FIXTURE), variant="60kwh")
        res = revision_resolutions()["VCU"]
        assert (res.revision, res.source) == ("uds", "detected")
        assert "22E001" in {str(k) for k in load_pids()["ecus"]["VCU"]["pids"]}

    def test_a_pin_outranks_it(self):
        cache.remember("variants", "60kwh", "VCU", "uds")
        set_active(str(FIXTURE), variant="60kwh", revisions=["VCU=kwp"])
        assert revision_resolutions()["VCU"].source == "flag"

    def test_a_detection_the_car_cannot_carry_is_ignored(self):
        cache.remember("variants", "40kwh", "VCU", "uds")
        set_active(str(FIXTURE), variant="40kwh")
        assert revision_resolutions()["VCU"].revision == "kwp"

    def test_forget(self):
        cache.remember("variants", None, "VCU", "kwp")
        assert cache.remembered("variants", "60kwh") == {"VCU": "kwp"}
        assert cache.forget("variants", None, "VCU")
        assert cache.remembered("variants", "60kwh") == {}

    def test_a_recording_is_attributed_detected(self):
        from canlib.variants.tags import capture_tag

        cache.remember("variants", "60kwh", "VCU", "kwp")
        tag = capture_tag(set_active(str(FIXTURE), variant="60kwh"))
        assert tag["attribution"]["revisions"] == {"VCU": "detected"}


class TestProbe:
    def test_an_ambiguous_ecu_is_detected_and_remembered(self):
        set_active(str(FIXTURE), variant="60kwh")
        term = FakeTerminal(answers={"22F187"})
        assert _detect(term, "VCU") is True
        assert term.sent == [(0x7E2, "1A90"), (0x7E2, "22F187")]
        assert revision_resolutions()["VCU"].revision == "uds"
        assert "22E001" in {str(k) for k in load_pids()["ecus"]["VCU"]["pids"]}
        assert cache.remembered("variants", "60kwh") == {"VCU": "uds"}
        # The next session already knows: nothing is sent.
        set_active(str(FIXTURE), variant="60kwh")
        again = FakeTerminal(answers={"22F187"})
        assert _detect(again, "VCU:21F0") is False and again.sent == []

    def test_the_first_positive_answer_is_enough(self):
        set_active(str(FIXTURE), variant="60kwh")
        term = FakeTerminal(answers={"1A90"})
        assert _detect(term, "VCU") is True
        assert term.sent == [(0x7E2, "1A90")]
        assert revision_resolutions()["VCU"].revision == "kwp"

    def test_a_silent_ecu_stays_undetermined(self, capsys):
        set_active(str(FIXTURE), variant="60kwh")
        assert _detect(FakeTerminal(), "VCU") is False
        assert revision_resolutions()["VCU"].revision is None
        assert cache.remembered("variants", "60kwh") == {}
        assert "stays undetermined" in capsys.readouterr().err

    def test_an_ecu_the_command_does_not_read_is_not_probed(self):
        set_active(str(FIXTURE), variant="60kwh")
        term = FakeTerminal(answers={"22F187"})
        assert _detect(term, "BMS:2101") is False and term.sent == []

    def test_a_determined_ecu_is_never_probed(self):
        set_active(str(FIXTURE), variant="40kwh")
        term = FakeTerminal(answers={"1A90"})
        assert _detect(term, "VCU") is False and term.sent == []

    def test_the_28kwh_ioniq_sends_no_probe(self):
        set_active("hyundai-ioniq-ae-ev", variant="28kwh")
        term = FakeTerminal(answers={"1A90"})
        assert _detect(term, "BMS", "@charging") is False and term.sent == []


class TestIdentity:
    def test_a_reading_names_the_revision_and_the_cars(self):
        from canlib.modes.revision_probe import describe_identity_report, identity_report

        set_active(str(FIXTURE))
        report = identity_report(0x7E2, {"1A90": True, "1A91": False})
        assert report["matches"] == ["kwp"] and report["variants"] == ["40kwh", "60kwh"]
        assert "kwp" in describe_identity_report(report)[0]
        report = identity_report(0x7E2, {"22F187": True})
        assert report["variants"] == ["60kwh"]
        assert cache.remembered("variants", None) == {"VCU": "uds"}

    def test_a_contradicting_reading_replaces_the_detection(self):
        from canlib.modes.revision_probe import identity_report

        cache.remember("variants", "60kwh", "VCU", "uds")
        set_active(str(FIXTURE), variant="60kwh")
        assert identity_report(0x7E2, {"1A90": True})["replaced"] == "uds"
        assert cache.remembered("variants", "60kwh") == {"VCU": "kwp"}

    def test_a_reading_matching_nothing_is_a_question(self):
        from canlib.modes.revision_probe import describe_identity_report, identity_report

        set_active(str(FIXTURE))
        report = identity_report(0x7E2, {"1A90": False})
        assert report["matches"] == []
        assert "doesn't know yet" in describe_identity_report(report)[0]

    def test_an_ecu_without_revisions_reports_nothing(self):
        from canlib.modes.revision_probe import identity_report

        set_active(str(FIXTURE))
        assert identity_report(0x7E4, {"1A90": True}) is None


def test_the_refusal_points_at_identity():
    from canlib.variants.selection import require_variant

    with pytest.raises(ProfileError, match="canair identity VCU"):
        require_variant(set_active(str(FIXTURE)), "talk to the car")


def test_validate_captures_flags_replies_outside_the_tagged_revision(tmp_path, capsys):
    from canlib.captures import io as capture_io
    from canlib.cli import main

    copy = tmp_path / "variants"
    shutil.copytree(FIXTURE, copy)
    path = copy / "captures" / "2026-01-01.json"
    data = json.loads(path.read_text())
    data["sessions"][2]["revisions"]["VCU"] = "uds"  # holds a KWP2000 2101 reply
    path.write_text(capture_io.capture_text(data))
    main(["--profile", str(copy), "validate", "captures"])
    assert "a reply to VCU 2101" in capsys.readouterr().out
