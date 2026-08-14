"""The transport-neutral frame-count ledger and its profile bridge.

Covers :mod:`canlib.frame_counts` (how evidence accumulates into a confirmed
count) and :mod:`canlib.response_frames` (how a confirmed count becomes a
``response_frames:`` edit, and how a stored one seeds a session).

The invariant under test throughout is that a count is only ever persisted from
evidence strong enough to have *tested* it, and is withdrawn the moment two
observations disagree — an over-count is merely slow, but an under-count leaves
the response's tail queued to answer the next request.
"""

from __future__ import annotations

import pytest

from canlib.frame_counts import (
    CONFIRMATIONS_REQUIRED,
    OBSERVATIONS_REQUIRED,
    FrameCountLedger,
    frames_for_payload,
)
from canlib.response_frames import FIELD, resolve_edits, seed_counts

_KEY = (0x7E4, "2101")
_OTHER = (0x7E4, "2105")


class TestFramesForPayload:
    """ISO-TP arithmetic: a single frame carries 7 bytes, the first of many 6."""

    @pytest.mark.parametrize(
        ("length", "frames"),
        [(1, 1), (3, 1), (7, 1), (8, 2), (13, 2), (14, 3), (20, 3), (27, 4), (28, 5)],
    )
    def test_known_lengths(self, length: int, frames: int):
        assert frames_for_payload(length) == frames

    def test_the_ioniq_bms_21f2_response_needs_thirteen_frames(self):
        # 90 bytes is the length that AGENTS.md pins as too long to request, so
        # this is the arithmetic behind that PID staying unoptimized.
        assert frames_for_payload(90) == 13


class TestLedgerEvidence:
    """What counts as enough evidence, and what withdraws it."""

    def test_a_single_observation_is_not_enough(self):
        ledger = FrameCountLedger()
        ledger.observe(_KEY, 4)
        assert ledger.confirmed() == {}

    def test_one_confirmation_is_enough(self):
        # A held digit is a direct test of the count: the adapter returned
        # exactly the requested number of frames and the reply validated.
        ledger = FrameCountLedger()
        ledger.observe(_KEY, 4)
        ledger.confirm(_KEY, 4)
        assert CONFIRMATIONS_REQUIRED == 1
        assert ledger.confirmed() == {_KEY: 4}

    def test_repeated_agreement_is_enough_without_a_digit(self):
        # The raw transport has no digit to hold, so its bar is agreement.
        ledger = FrameCountLedger()
        for _ in range(OBSERVATIONS_REQUIRED):
            ledger.observe(_KEY, 3)
        assert ledger.confirmed() == {_KEY: 3}

    def test_agreement_short_of_the_bar_is_not_enough(self):
        ledger = FrameCountLedger()
        for _ in range(OBSERVATIONS_REQUIRED - 1):
            ledger.observe(_KEY, 3)
        assert ledger.confirmed() == {}

    def test_disagreement_retires_a_count_that_had_already_qualified(self):
        ledger = FrameCountLedger()
        ledger.observe(_KEY, 4)
        ledger.confirm(_KEY, 4)
        assert ledger.confirmed() == {_KEY: 4}

        ledger.observe(_KEY, 5)
        assert ledger.confirmed() == {}
        assert _KEY in ledger.retired()

    def test_disagreement_is_permanent_within_a_session(self):
        # Once a request is known to vary, further agreement must not rehabilitate
        # it — the variation is the fact, and any single count is an undercount
        # for some responses.
        ledger = FrameCountLedger()
        ledger.observe(_KEY, 4)
        ledger.observe(_KEY, 5)
        for _ in range(OBSERVATIONS_REQUIRED + 2):
            ledger.observe(_KEY, 4)
        assert ledger.confirmed() == {}
        assert _KEY in ledger.retired()

    def test_mark_conflict_retires_a_never_observed_key(self):
        # opt_out() calls this when the digit was convicted, so the stored profile
        # value must be cleared even though this session observed no plain reply.
        ledger = FrameCountLedger()
        ledger.mark_conflict(_KEY)
        assert _KEY in ledger.retired()
        assert ledger.confirmed() == {}

    def test_keys_are_independent(self):
        ledger = FrameCountLedger()
        ledger.observe(_KEY, 4)
        ledger.confirm(_KEY, 4)
        ledger.observe(_OTHER, 2)
        ledger.observe(_OTHER, 7)
        assert ledger.confirmed() == {_KEY: 4}
        assert ledger.retired() == {_OTHER}

    def test_clear_forgets_everything(self):
        ledger = FrameCountLedger()
        ledger.observe(_KEY, 4)
        ledger.confirm(_KEY, 4)
        ledger.mark_conflict(_OTHER)
        ledger.clear()
        assert ledger.confirmed() == {}
        assert ledger.retired() == set()


def _profile(pids: dict) -> dict:
    """A minimal pids_data with one ECU at 0x7E4.

    Takes the PID mapping positionally because ``ecus/`` YAML leaves numeric PIDs
    unquoted, so a realistic key is an ``int`` and cannot be a keyword.
    """
    return {"ecus": {"BMS": {"tx_id": 0x7E4, "pids": pids}}}


class TestSeedCounts:
    """Turning stored ``response_frames:`` into session seeds."""

    def test_a_stored_count_is_keyed_by_tx_id_and_request(self):
        data = _profile({"2101": {"status": "active", FIELD: 4}})
        assert seed_counts(data) == {(0x7E4, "2101"): 4}

    def test_a_pid_without_a_count_is_not_seeded(self):
        data = _profile({"2101": {"status": "active"}})
        assert seed_counts(data) == {}

    def test_a_variable_length_pid_is_never_seeded(self):
        data = _profile({"2101": {"status": "active", "variable_length": True, FIELD: 4}})
        assert seed_counts(data) == {}

    def test_an_ignored_pid_is_not_seeded(self):
        data = _profile({"2101": {"status": "ignored", FIELD: 4}})
        assert seed_counts(data) == {}

    def test_an_int_pid_key_is_normalized_the_way_the_ecu_index_does(self):
        # ecus/ YAML leaves numeric PIDs unquoted, so the key arrives as an int;
        # the request half of a CountKey is the upper-cased string form.
        data = _profile({2101: {"status": "active", FIELD: 4}})
        assert seed_counts(data) == {(0x7E4, "2101"): 4}

    def test_a_lowercase_hex_pid_is_upper_cased(self):
        data = _profile({"21f2": {"status": "active", FIELD: 3}})
        assert seed_counts(data) == {(0x7E4, "21F2"): 3}

    def test_a_count_too_large_to_request_is_still_seeded(self):
        # The magnitude ceiling belongs to the transport that has to emit the
        # nibble, not to the profile reader — a profile fact is reported as-is.
        data = _profile({"21F2": {"status": "active", FIELD: 13}})
        assert seed_counts(data) == {(0x7E4, "21F2"): 13}


class TestResolveEdits:
    """Turning a session's ledger back into profile edits."""

    def test_a_confirmed_count_becomes_an_edit(self):
        data = _profile({"2101": {"status": "active"}})
        ledger = FrameCountLedger()
        ledger.observe((0x7E4, "2101"), 4)
        ledger.confirm((0x7E4, "2101"), 4)

        edits, notes = resolve_edits(data, ledger)
        assert notes == []
        assert [(e.ecu, e.pid, e.frames, e.previous) for e in edits] == [("BMS", "2101", 4, None)]

    def test_an_unchanged_count_produces_no_edit(self):
        # Steady state must not touch a single file, or every session dirties the
        # working tree for nothing.
        data = _profile({"2101": {"status": "active", FIELD: 4}})
        ledger = FrameCountLedger()
        ledger.observe((0x7E4, "2101"), 4)
        ledger.confirm((0x7E4, "2101"), 4)

        edits, _ = resolve_edits(data, ledger)
        assert edits == []

    def test_a_changed_count_is_rewritten(self):
        data = _profile({"2101": {"status": "active", FIELD: 3}})
        ledger = FrameCountLedger()
        ledger.observe((0x7E4, "2101"), 4)
        ledger.confirm((0x7E4, "2101"), 4)

        edits, _ = resolve_edits(data, ledger)
        assert [(e.pid, e.frames, e.previous) for e in edits] == [("2101", 4, 3)]

    def test_a_retired_count_clears_the_stored_value(self):
        data = _profile({"2101": {"status": "active", FIELD: 4}})
        ledger = FrameCountLedger()
        ledger.observe((0x7E4, "2101"), 4)
        ledger.observe((0x7E4, "2101"), 6)

        edits, _ = resolve_edits(data, ledger)
        assert len(edits) == 1
        assert edits[0].cleared
        assert edits[0].frames is None
        assert edits[0].previous == 4

    def test_retiring_a_pid_that_stores_nothing_is_a_no_op(self):
        data = _profile({"2101": {"status": "active"}})
        ledger = FrameCountLedger()
        ledger.observe((0x7E4, "2101"), 4)
        ledger.observe((0x7E4, "2101"), 6)

        edits, _ = resolve_edits(data, ledger)
        assert edits == []

    def test_a_count_learned_on_an_unknown_request_is_ignored(self):
        # A multi-DID batch request ("22" + several DIDs) matches no single PID.
        data = _profile({"2101": {"status": "active"}})
        ledger = FrameCountLedger()
        ledger.observe((0x7E4, "22C00B1234"), 4)
        ledger.confirm((0x7E4, "22C00B1234"), 4)

        edits, notes = resolve_edits(data, ledger)
        assert edits == []
        assert notes == []

    def test_a_count_learned_on_an_unknown_ecu_is_ignored(self):
        data = _profile({"2101": {"status": "active"}})
        ledger = FrameCountLedger()
        ledger.observe((0x700, "2101"), 4)
        ledger.confirm((0x700, "2101"), 4)

        edits, _ = resolve_edits(data, ledger)
        assert edits == []

    def test_a_shared_tx_header_is_refused_and_reported(self):
        # Under normal_extended_11bit several ECUs answer on one 11-bit header and
        # are told apart by a target-address byte the CountKey does not carry, so
        # the count cannot be attributed to either ECU.
        data = {
            "ecus": {
                "DME": {"tx_id": 0x6F1, "pids": {"2101": {"status": "active"}}},
                "EGS": {"tx_id": 0x6F1, "pids": {"2101": {"status": "active"}}},
            }
        }
        ledger = FrameCountLedger()
        ledger.observe((0x6F1, "2101"), 4)
        ledger.confirm((0x6F1, "2101"), 4)

        edits, notes = resolve_edits(data, ledger)
        assert edits == []
        assert len(notes) == 1
        assert "0x6F1" in notes[0]
        assert "DME" in notes[0] and "EGS" in notes[0]

    def test_a_variable_length_pid_is_never_written(self):
        data = _profile({"2101": {"status": "active", "variable_length": True}})
        ledger = FrameCountLedger()
        ledger.observe((0x7E4, "2101"), 4)
        ledger.confirm((0x7E4, "2101"), 4)

        edits, _ = resolve_edits(data, ledger)
        assert edits == []

    def test_describe_names_the_pid_and_the_transition(self):
        data = _profile({"2101": {"status": "active", FIELD: 3}})
        ledger = FrameCountLedger()
        ledger.observe((0x7E4, "2101"), 4)
        ledger.confirm((0x7E4, "2101"), 4)

        (edit,) = resolve_edits(data, ledger)[0]
        text = edit.describe()
        assert "BMS" in text and "2101" in text and "4" in text
