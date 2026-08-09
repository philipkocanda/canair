"""Tests for the session vehicle_states lint (vocabulary + exclusivity).

A session tagged with a mutually exclusive pair (``excludes:`` in
vehicle_states.yaml) did not overlap those states, it sequenced through them —
so its tokens describe a span of time, and without a ``state_spans`` timeline
every capture in it answers for states it was never in. That is the soft warning
here; a session that carries a timeline is expected to hold the pair.
"""

import json
import textwrap

import yaml

from canlib.commands.validate import _capture_state_warnings
from canlib.states import StateRule

VOCAB = {"charging", "driving", "parked", "plugged", "ready"}
RULES = [StateRule(name="DRIVING", excludes=("PARKED", "CHARGING"))]


def _write(tmp_path, body: str):
    p = tmp_path / "2026-08-08.json"
    p.write_text(json.dumps(yaml.safe_load(textwrap.dedent(body))))
    return p


def _session(states: str, extra: str = "") -> str:
    return f"""
        sessions:
          - date: "2026-08-08"
            label: "run"
            vehicle_states: {states}
{extra}
            captures:
              - {{rx: "0x7EC", pid: "2101", payload: "6101AA", time: "10:00:00"}}
        """


class TestVocabulary:
    """The pre-existing check: a session with no recognizable token at all."""

    def test_unknown_tokens_warn(self, tmp_path):
        warnings = _capture_state_warnings(_write(tmp_path, _session("[banana]")), VOCAB, RULES)
        assert len(warnings) == 1
        assert "no token in the vehicle_states.yaml vocabulary" in str(warnings[0])

    def test_one_known_token_is_enough(self, tmp_path):
        path = _write(tmp_path, _session("[banana, ready]"))
        assert _capture_state_warnings(path, VOCAB, RULES) == []

    def test_non_list_warns(self, tmp_path):
        warnings = _capture_state_warnings(_write(tmp_path, _session('"ready"')), VOCAB, RULES)
        assert len(warnings) == 1
        assert "must be a list" in str(warnings[0])


class TestExclusivity:
    def test_exclusive_pair_without_spans_warns(self, tmp_path):
        path = _write(tmp_path, _session("[driving, parked]"))
        warnings = _capture_state_warnings(path, VOCAB, RULES)
        assert len(warnings) == 1
        assert "mutually exclusive pair DRIVING+PARKED" in str(warnings[0])
        assert "--backfill-state-spans" in str(warnings[0])

    def test_a_timeline_makes_the_pair_expected(self, tmp_path):
        spans = """
            state_spans:
              source: record
              spans:
                - {at: "10:00:00", states: [DRIVING]}
                - {at: "10:30:00", states: [PARKED]}"""
        path = _write(tmp_path, _session("[driving, parked]", spans))
        assert _capture_state_warnings(path, VOCAB, RULES) == []

    def test_an_empty_spans_list_still_warns(self, tmp_path):
        path = _write(
            tmp_path,
            _session("[driving, parked]", "\n            state_spans:\n              spans: []"),
        )
        warnings = _capture_state_warnings(path, VOCAB, RULES)
        assert len(warnings) == 1
        assert "mutually exclusive pair" in str(warnings[0])

    def test_each_exclusive_pair_is_reported(self, tmp_path):
        path = _write(tmp_path, _session("[driving, parked, charging]"))
        messages = " ".join(str(w) for w in _capture_state_warnings(path, VOCAB, RULES))
        assert "CHARGING+DRIVING" in messages
        assert "DRIVING+PARKED" in messages

    def test_simultaneous_overlap_is_not_flagged(self, tmp_path):
        """READY+PARKED is a real reading — nothing declares them exclusive."""
        path = _write(tmp_path, _session("[ready, parked]"))
        assert _capture_state_warnings(path, VOCAB, RULES) == []

    def test_no_excludes_declared_disables_the_check(self, tmp_path):
        path = _write(tmp_path, _session("[driving, parked]"))
        assert _capture_state_warnings(path, VOCAB, []) == []

    def test_matching_is_case_insensitive_both_ways(self, tmp_path):
        path = _write(tmp_path, _session("[Driving, PARKED]"))
        assert len(_capture_state_warnings(path, VOCAB, RULES)) == 1
