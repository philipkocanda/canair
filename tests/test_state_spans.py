"""Tests for canlib.state_spans — the temporal vehicle-state timeline primitive."""

from canlib.state_spans import (
    build_spans,
    parse_time_key,
    span_keys,
    span_state_union,
    states_at,
)


class TestParseTimeKey:
    def test_hh_mm_ss_fff(self):
        assert parse_time_key("15:47:56.192") == 15 * 3600 + 47 * 60 + 56.192

    def test_hh_mm_ss(self):
        assert parse_time_key("01:02:03") == 3723.0

    def test_hh_mm(self):
        assert parse_time_key("10:30") == 37800.0

    def test_unpadded_hour_orders_correctly(self):
        # The reason spans compare on a numeric key rather than lexicographically:
        # "9:00:00" > "10:00:00" as strings, but 9am precedes 10am.
        assert parse_time_key("9:00:00") < parse_time_key("10:00:00")

    def test_rejects_garbage(self):
        assert parse_time_key(None) is None
        assert parse_time_key("") is None
        assert parse_time_key("not-a-time") is None
        assert parse_time_key("10") is None
        assert parse_time_key("10:xx:00") is None


class TestBuildSpans:
    def test_coalesces_consecutive_equal_states(self):
        spans = build_spans(
            [
                ("10:00:00", ["DRIVING", "READY"]),
                ("10:00:10", ["DRIVING", "READY"]),
                ("10:00:20", ["DRIVING", "READY"]),
                ("10:05:00", ["PARKED"]),
            ]
        )
        assert spans == [
            {"at": "10:00:00", "states": ["DRIVING", "READY"]},
            {"at": "10:05:00", "states": ["PARKED"]},
        ]

    def test_reoccurring_state_set_starts_a_new_span(self):
        # Drive -> charge -> drive is three spans, not two: the timeline is a
        # sequence, not a set.
        spans = build_spans(
            [
                ("10:00:00", ["DRIVING"]),
                ("11:00:00", ["CHARGING"]),
                ("12:00:00", ["DRIVING"]),
            ]
        )
        assert [s["at"] for s in spans] == ["10:00:00", "11:00:00", "12:00:00"]

    def test_sorts_unordered_observations(self):
        spans = build_spans([("12:00:00", ["B"]), ("10:00:00", ["A"])])
        assert [s["at"] for s in spans] == ["10:00:00", "12:00:00"]

    def test_empty_states_is_a_real_span(self):
        spans = build_spans([("10:00:00", ["DRIVING"]), ("10:01:00", [])])
        assert spans[1] == {"at": "10:01:00", "states": []}

    def test_drops_untimed_observations(self):
        spans = build_spans([(None, ["DRIVING"]), ("10:00:00", ["PARKED"])])
        assert spans == [{"at": "10:00:00", "states": ["PARKED"]}]

    def test_empty_input(self):
        assert build_spans([]) == []


_TRIP_SPANS = [
    {"at": "10:00:00", "states": ["DRIVING", "READY"]},
    {"at": "11:00:00", "states": ["CHARGING", "PLUGGED"]},
    {"at": "12:00:00", "states": ["DRIVING", "READY"]},
]


class TestStatesAt:
    SPANS = _TRIP_SPANS

    def test_inside_a_span(self):
        assert states_at(self.SPANS, "11:30:00") == ["CHARGING", "PLUGGED"]

    def test_exact_boundary_belongs_to_the_span_it_starts(self):
        assert states_at(self.SPANS, "11:00:00") == ["CHARGING", "PLUGGED"]

    def test_just_before_a_boundary_belongs_to_the_previous_span(self):
        assert states_at(self.SPANS, "10:59:59.999") == ["DRIVING", "READY"]

    def test_after_the_last_span_holds(self):
        # Half-open intervals: the final span runs to the end of the session.
        assert states_at(self.SPANS, "23:59:59") == ["DRIVING", "READY"]

    def test_before_the_first_span_is_unresolvable(self):
        assert states_at(self.SPANS, "09:59:59") is None

    def test_untimed_capture_is_unresolvable(self):
        assert states_at(self.SPANS, None) is None
        assert states_at(self.SPANS, "") is None

    def test_no_spans_is_unresolvable(self):
        assert states_at([], "11:00:00") is None

    def test_empty_span_is_an_answer_not_a_miss(self):
        spans = [{"at": "10:00:00", "states": []}]
        assert states_at(spans, "10:30:00") == []

    def test_precomputed_keys_agree(self):
        keys = span_keys(self.SPANS)
        assert keys == [36000.0, 39600.0, 43200.0]
        assert states_at(self.SPANS, "11:30:00", keys) == ["CHARGING", "PLUGGED"]


class TestSpanStateUnion:
    def test_union_over_spans(self):
        spans = [
            {"at": "10:00:00", "states": ["DRIVING", "READY"]},
            {"at": "11:00:00", "states": ["CHARGING", "PLUGGED", "PARKED"]},
        ]
        assert span_state_union(spans) == {"DRIVING", "READY", "CHARGING", "PLUGGED", "PARKED"}

    def test_empty(self):
        assert span_state_union([]) == set()
        assert span_state_union([{"at": "10:00:00", "states": []}]) == set()


class TestRoundTrip:
    def test_every_observation_resolves_to_its_own_states(self):
        # The invariant a back-fill relies on: resolving an observation's own
        # timestamp through the spans it produced returns its own state set.
        observations = [
            ("10:00:00", ["DRIVING"]),
            ("10:00:10", ["DRIVING"]),
            ("10:30:00", ["PARKED"]),
            ("10:31:00", ["CHARGING", "PARKED"]),
            ("11:00:00", []),
            ("11:30:00", ["DRIVING"]),
        ]
        spans = build_spans(observations)
        keys = span_keys(spans)
        for at, states in observations:
            assert states_at(spans, at, keys) == states

    def test_union_matches_the_observed_states(self):
        observations = [("10:00:00", ["DRIVING", "READY"]), ("11:00:00", ["CHARGING"])]
        spans = build_spans(observations)
        expected = {s for _at, states in observations for s in states}
        assert span_state_union(spans) == expected
