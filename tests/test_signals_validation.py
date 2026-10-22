"""check_signals_doc: the type, range and layout rules a signals/ map must meet.

Each rule here guards an `export dbc` (or decode) that would otherwise crash or write a
DBC cantools cannot decode, so a value the validator accepts must be exportable.
"""

from __future__ import annotations

import pytest

from canlib.validation.signals import (
    check_signals_doc,
    frame_length_for,
    is_message_length,
    message_length,
)


def _doc(mid="0x1", msg=None, **sig):
    return {
        "messages": {mid: {**(msg or {}), "signals": {"X": {"start_bit": 0, "length": 8, **sig}}}}
    }


def _errors(doc) -> list[str]:
    return check_signals_doc(doc)[0]


class TestSignalFieldTypes:
    @pytest.mark.parametrize(
        ("field", "value", "fragment"),
        [
            ("start_bit", "a", "start_bit must be an integer"),
            ("start_bit", True, "start_bit must be an integer"),
            ("start_bit", 1.5, "start_bit must be an integer"),
            ("length", "8", "length must be an integer"),
            ("length", False, "length must be an integer"),
            ("scale", "0.5", "scale must be a number"),
            ("scale", True, "scale must be a number"),
            ("offset", "1", "offset must be a number"),
            ("min", "0", "min must be a number"),
            ("max", None, "max must be a number"),
            ("verified", "yes", "verified must be true|false"),
            ("float", 1, "float must be true|false"),
            ("unit", 3, "unit must be a string"),
            ("notes", ["a"], "notes must be a string"),
            ("byte_order", None, "byte_order must be little|big"),
        ],
    )
    def test_wrong_type_rejected(self, field, value, fragment):
        errors = _errors(_doc(**{field: value}))
        assert any(fragment in e for e in errors), errors

    @pytest.mark.parametrize(
        "sig",
        [
            {"scale": 0.5, "offset": -40, "min": 0, "max": 1.5},
            {"scale": 2, "verified": True, "unit": "km/h"},
            {"signed": False},
        ],
    )
    def test_good_types_accepted(self, sig):
        assert _errors(_doc(**sig)) == []

    def test_scale_zero_rejected(self):
        assert any("scale must not be 0" in e for e in _errors(_doc(scale=0)))
        assert any("scale must not be 0" in e for e in _errors(_doc(scale=0.0)))


class TestTypedMaps:
    def test_values_must_be_int_keyed_mapping(self):
        assert any(
            "'values' must be a mapping" in e for e in _errors(_doc(type="enum", values=[1, 2]))
        )
        errors = _errors(_doc(type="enum", values={"P": "park"}))
        assert any("'values' key 'P' must be an integer" in e for e in errors)

    def test_labels_must_be_strings(self):
        # A bare `on` in YAML is a boolean: refuse it rather than label a state "True".
        errors = _errors(_doc(type="enum", values={0: "off", 1: True}))
        assert any("label for 1 must be a string" in e for e in errors)

    def test_quoted_int_keys_accepted(self):
        # The typed decoder reads int and str keys alike (canlib/decode_value.py).
        assert _errors(_doc(type="enum", values={"1": "P", 2: "R"})) == []

    def test_bits_must_be_mapping_in_range(self):
        assert any("'bits' must be a mapping" in e for e in _errors(_doc(type="bitmask", bits="x")))
        assert any("out of range" in e for e in _errors(_doc(type="bitmask", bits={64: "a"})))

    def test_map_needs_its_type(self):
        assert any("only valid with type 'enum'" in e for e in _errors(_doc(values={1: "a"})))
        assert any("requires a 'values:' map" in e for e in _errors(_doc(type="enum")))
        assert any("requires a 'bits:' map" in e for e in _errors(_doc(type="bitmask")))


class TestFloat:
    @pytest.mark.parametrize("length", [32, 64])
    def test_float_lengths_accepted(self, length):
        doc = _doc(length=length, float=True)
        doc["messages"]["0x1"]["length"] = 8
        assert _errors(doc) == []

    def test_float_needs_32_or_64_bits(self):
        assert any("32 or 64 bits" in e for e in _errors(_doc(length=16, float=True)))


class TestMessageLayout:
    def test_length_shorter_than_signals_rejected(self):
        errors = _errors(_doc(msg={"length": 1}, start_bit=8, length=8))
        assert any("length 1 is shorter than its signals need (2 bytes for X)" in e for e in errors)

    def test_big_endian_span_uses_dbc_numbering(self):
        # 39|16@0 occupies bytes 4-5: a 6-byte frame holds it, a 5-byte one does not.
        sig = {"start_bit": 39, "length": 16, "byte_order": "big"}
        assert _errors(_doc(msg={"length": 6}, **sig)) == []
        assert any("shorter than its signals" in e for e in _errors(_doc(msg={"length": 5}, **sig)))

    def test_zero_length_with_signals_rejected(self):
        assert any("length 0 is shorter" in e for e in _errors(_doc(msg={"length": 0})))

    def test_undeclared_length_must_fit_a_frame(self):
        assert any("64 bytes are shorter" in e for e in _errors(_doc(start_bit=512)))

    @pytest.mark.parametrize("mid", ["0x1FFFFFFF", "0x0"])
    def test_id_range_accepted(self, mid):
        assert _errors(_doc(mid=mid)) == []

    def test_id_above_29_bits_rejected(self):
        assert any("29-bit maximum" in e for e in _errors(_doc(mid="0x20000000")))

    def test_extended_flag(self):
        assert _errors(_doc(mid="0x123", msg={"extended": True})) == []
        errors = _errors(_doc(mid="0x800", msg={"extended": False}))
        assert any("standard (11-bit) frame id must be <= 0x7FF" in e for e in errors)
        assert any("extended must be true|false" in e for e in _errors(_doc(msg={"extended": 1})))

    def test_fd_flag(self):
        assert _errors(_doc(msg={"fd": True, "length": 8})) == []
        errors = _errors(_doc(msg={"fd": False, "length": 12}))
        assert any("classic (non-FD) frame carries at most 8 bytes" in e for e in errors)

    def test_name_must_be_string(self):
        assert any("name must be a string" in e for e in _errors(_doc(msg={"name": 123})))


class TestLengthHelpers:
    @pytest.mark.parametrize(
        ("span", "length"), [(0, 1), (1, 1), (8, 8), (9, 12), (12, 12), (13, 16), (33, 48)]
    )
    def test_frame_length_for(self, span, length):
        assert frame_length_for(span) == length

    @pytest.mark.parametrize("n", [0, 5, 8, 12, 16, 20, 24, 32, 48, 64])
    def test_is_message_length(self, n):
        assert is_message_length(n)

    @pytest.mark.parametrize("n", [-1, 9, 10, 11, 63, 65, True, "8", 8.0])
    def test_is_not_message_length(self, n):
        assert not is_message_length(n)

    def test_message_length_falls_back_to_span(self):
        msg = {"signals": {"A": {"start_bit": 64, "length": 8}}}
        assert message_length(msg) == 12  # 9 bytes needs the next CAN FD size
        assert message_length({**msg, "length": 16}) == 16
