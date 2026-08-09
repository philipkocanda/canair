"""Tests for canlib.state_infer — offline session-state inference."""

from canlib.state_infer import (
    _bucket_cycles,
    infer_session_states,
    infer_state_spans,
)
from canlib.states import StateRule, compile_predicate


def _cap(ecu, pid, payload, *, date="2026-04-14", time=None):
    e = {"ecu": ecu, "pid": pid, "payload": payload, "date": date}
    if time is not None:
        e["time"] = time
    return e


# A tiny ECU index: BMS 2101 B02 as a signed current, VCU 2101 B02 as ready flag.
def _ecu_index():
    return {
        "BMS": {
            "tx_id": "0x7E4",
            "pids": {
                "2101": {
                    "parameters": {
                        "CURRENT": {"expression": "S3", "verified": True},
                    }
                }
            },
        },
        "VCU": {
            "tx_id": "0x7E2",
            "pids": {
                "2101": {
                    "parameters": {
                        "READY": {"expression": "B3", "verified": True},
                    }
                }
            },
        },
    }


def _rules():
    return [
        StateRule("CHARGING", predicate=compile_predicate("BMS.CURRENT < -1 or OBC.A > 0.5")),
        StateRule("READY", predicate=compile_predicate("VCU.READY == 1")),
        StateRule("SLEEP", predicate=None),
    ]


class TestBucketCycles:
    def test_untimed_single_bucket(self):
        caps = [_cap("BMS", "2101", "6101FF"), _cap("VCU", "2101", "610101")]
        cycles, timed = _bucket_cycles(caps, 10.0)
        assert timed is False
        assert len(cycles) == 1
        assert len(cycles[0]) == 2

    def test_timed_windowing(self):
        caps = [
            _cap("BMS", "2101", "6101FF", time="10:00:00"),
            _cap("VCU", "2101", "610101", time="10:00:03"),  # within 10s -> same cycle
            _cap("BMS", "2101", "6101FF", time="10:00:30"),  # >10s -> new cycle
        ]
        cycles, timed = _bucket_cycles(caps, 10.0)
        assert timed is True
        assert [len(c) for c in cycles] == [2, 1]

    def test_boundary_exact_tol(self):
        caps = [
            _cap("BMS", "2101", "6101FF", time="10:00:00"),
            _cap("VCU", "2101", "610101", time="10:00:10"),  # exactly 10s -> same
        ]
        cycles, _ = _bucket_cycles(caps, 10.0)
        assert len(cycles) == 1


class TestInferSessionStates:
    def test_ready_from_vcu(self):
        # VCU.READY B3 == 1  (payload 61 01 01: PCI-stripped SID 61, PID 01, data 01)
        caps = [_cap("VCU", "2101", "610101")]
        inf = infer_session_states(caps, _rules(), _ecu_index())
        assert inf.inferred == ["READY"]
        assert inf.timed is False

    def test_charging_from_bms_negative_current(self):
        # BMS.CURRENT S3 signed: 0xFB = -5
        caps = [_cap("BMS", "2101", "6101FB")]
        inf = infer_session_states(caps, _rules(), _ecu_index())
        assert inf.inferred == ["CHARGING"]

    def test_composite_union_across_cycles(self):
        # cycle 1: charging; cycle 2 (>10s later): ready — union both.
        caps = [
            _cap("BMS", "2101", "6101FB", time="10:00:00"),
            _cap("VCU", "2101", "610101", time="10:05:00"),
        ]
        inf = infer_session_states(caps, _rules(), _ecu_index())
        assert set(inf.inferred) == {"CHARGING", "READY"}
        assert inf.n_cycles == 2

    def test_definitely_false(self):
        # VCU.READY == 0 -> READY provably false; nothing matched.
        caps = [_cap("VCU", "2101", "610100")]
        inf = infer_session_states(caps, _rules(), _ecu_index())
        assert inf.inferred == []
        assert "READY" in inf.definitely_false

    def test_no_decodable_params_undetermined(self):
        caps = [_cap("UNKNOWN", "9999", "7F2212")]
        inf = infer_session_states(caps, _rules(), _ecu_index())
        assert inf.inferred == []
        assert inf.n_decoded_params == 0

    def test_match_wins_over_false_in_other_cycle(self):
        # ready in one cycle, not-ready in another -> READY inferred, not false.
        caps = [
            _cap("VCU", "2101", "610101", time="10:00:00"),
            _cap("VCU", "2101", "610100", time="10:05:00"),
        ]
        inf = infer_session_states(caps, _rules(), _ecu_index())
        assert inf.inferred == ["READY"]
        assert "READY" not in inf.definitely_false


def _span_ecu_index():
    """Like :func:`_ecu_index` but with the flag on a real *data* byte.

    WiCAN ``Bnn`` counts the ISO-TP PCI byte, so ``B3`` on a ``62 01 01 …``
    response is the DID echo's low byte — constant. ``B4`` is the first payload
    byte, which is what a flag actually lives on.
    """
    return {
        "BMS": {
            "tx_id": "0x7E4",
            "pids": {"2101": {"parameters": {"CURRENT": {"expression": "S4"}}}},
        },
        "VCU": {
            "tx_id": "0x7E2",
            "pids": {"2101": {"parameters": {"READY": {"expression": "B4"}}}},
        },
    }


def _span_rules():
    """Rules with ``expr`` set — :func:`infer_state_spans` needs it to know which
    signals each predicate reads (freshness), which ``load_states`` always fills in.
    """
    return [
        StateRule(
            "CHARGING",
            predicate=compile_predicate("BMS.CURRENT < -1"),
            expr="BMS.CURRENT < -1",
        ),
        StateRule("READY", predicate=compile_predicate("VCU.READY == 1"), expr="VCU.READY == 1"),
        StateRule("SLEEP", predicate=None),
    ]


class TestInferStateSpans:
    def test_a_drive_then_charge_becomes_two_spans(self):
        caps = [
            _cap("VCU", "2101", "62010101", time="10:00:00"),  # READY=1
            _cap("VCU", "2101", "62010100", time="10:05:00"),  # READY=0
            _cap("BMS", "2101", "620101F0", time="10:05:01"),  # CURRENT=-16
        ]
        inf = infer_state_spans(caps, _span_rules(), _span_ecu_index())
        assert [s["states"] for s in inf.spans] == [["READY"], ["CHARGING"]]
        assert [s["at"] for s in inf.spans] == ["10:00:00", "10:05:00"]
        assert inf.union == ["CHARGING", "READY"]

    def test_state_latches_across_a_cycle_that_cannot_see_it(self):
        """A cycle polling only the *other* ECU must not end the state."""
        caps = [
            _cap("VCU", "2101", "62010101", time="10:00:00"),
            _cap("BMS", "2101", "62010101", time="10:01:00"),  # CURRENT=+1, READY unseen
            _cap("VCU", "2101", "62010101", time="10:02:00"),
        ]
        inf = infer_state_spans(caps, _span_rules(), _span_ecu_index())
        assert [s["states"] for s in inf.spans] == [["READY"]]

    def test_fresh_evidence_retracts_even_without_a_definite_false(self):
        """READY=0 is a definite false; the point is it ends the span at that time."""
        caps = [
            _cap("VCU", "2101", "62010101", time="10:00:00"),
            _cap("VCU", "2101", "62010100", time="10:10:00"),
        ]
        inf = infer_state_spans(caps, _span_rules(), _span_ecu_index())
        assert [(s["at"], s["states"]) for s in inf.spans] == [
            ("10:00:00", ["READY"]),
            ("10:10:00", []),
        ]

    def test_a_cycle_with_no_relevant_signal_is_not_an_observation(self):
        caps = [_cap("AAF", "2101", "620101FF", time="10:00:00")]
        inf = infer_state_spans(caps, _span_rules(), _span_ecu_index())
        assert inf.spans == []
        assert inf.n_informative == 0

    def test_untimed_captures_are_counted_not_placed(self):
        caps = [_cap("VCU", "2101", "62010101")]
        inf = infer_state_spans(caps, _span_rules(), _span_ecu_index())
        assert inf.spans == []
        assert inf.n_untimed == 1

    def test_a_predicateless_state_never_appears(self):
        caps = [_cap("VCU", "2101", "62010101", time="10:00:00")]
        inf = infer_state_spans(caps, _span_rules(), _span_ecu_index())
        assert "SLEEP" not in inf.union
