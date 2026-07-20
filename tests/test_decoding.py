"""Tests for canlib.decoding — decode_param_rows."""

from canlib.decoding import decode_param_rows


class TestDecodeParamRows:
    def test_empty_parameters(self):
        assert decode_param_rows("6101FF", {}) == []

    def test_bad_hex_returns_empty(self):
        params = {"X": {"expression": "B0"}}
        # Odd-length / invalid hex can't be parsed → empty list, no raise.
        assert decode_param_rows("ZZZ", params) == []

    def test_skips_params_without_expression(self):
        params = {"NO_EXPR": {"unit": "V"}, "OK": {"expression": "B0"}}
        rows = decode_param_rows("62B001", params)
        names = [r[0] for r in rows]
        assert names == ["OK"]

    def test_row_shape_and_values(self):
        # "62B001" -> WiCAN frame "0362B001" (B0 is the PCI length byte),
        # so B1 is the first payload byte 0x62 = 98.
        params = {"FIRST_BYTE": {"expression": "B1", "unit": "x", "verified": True}}
        rows = decode_param_rows("62B001", params)
        assert len(rows) == 1
        name, value, unit, expr, error, verified, display = rows[0]
        assert name == "FIRST_BYTE"
        assert value == 0x62  # first payload byte (after PCI)
        assert unit == "x"
        assert expr == "B1"
        assert error is None
        assert verified is True
        assert display == ""

    def test_value_rounded_to_two_decimals(self):
        params = {"THIRD": {"expression": "B2/3"}}  # 0xB0/3 = 58.666...
        rows = decode_param_rows("62B001", params)
        assert rows[0][1] == 58.67

    def test_expression_error_captured(self):
        params = {"BAD": {"expression": "B99"}}  # index out of range
        rows = decode_param_rows("62B001", params)
        name, value, _unit, _expr, error, _verified, _display = rows[0]
        assert name == "BAD"
        assert value is None
        assert error is not None

    def test_display_field_passed_through(self):
        params = {"T": {"expression": "B0", "display": "f'{int(v)}!'"}}
        rows = decode_param_rows("62B001", params)
        assert rows[0][6] == "f'{int(v)}!'"

    def test_multiple_params_preserve_order(self):
        params = {
            "A": {"expression": "B0"},
            "B": {"expression": "B1"},
            "C": {"expression": "B2"},
        }
        rows = decode_param_rows("62B001", params)
        assert [r[0] for r in rows] == ["A", "B", "C"]
