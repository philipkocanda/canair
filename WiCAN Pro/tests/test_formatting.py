"""Tests for canlib.formatting — output formatting helpers."""

from canlib.formatting import format_value


class TestFormatValue:
    def test_integer_value(self):
        assert format_value(42.0, "km/h") == "42 km/h"

    def test_float_value(self):
        assert format_value(3.14, "V") == "3.14 V"

    def test_no_unit(self):
        assert format_value(100.0, "") == "100"

    def test_zero(self):
        assert format_value(0.0, "%") == "0 %"

    def test_negative(self):
        assert format_value(-40.0, "°C") == "-40 °C"

    def test_small_decimal(self):
        assert format_value(12.50, "A") == "12.50 A"
