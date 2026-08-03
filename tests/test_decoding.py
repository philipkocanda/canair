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


class TestTypedParams:
    def test_enum_label_via_display(self):
        # B1 = 0x62 = 98; map that value to a label.
        params = {
            "MODE": {
                "expression": "B1",
                "type": "enum",
                "values": {98: "heat_pump", 0: "off"},
            }
        }
        rows = decode_param_rows("62B001", params)
        name, value, _unit, expr, error, _verified, display = rows[0]
        assert name == "MODE"
        assert value == 0x62  # raw float preserved for numeric consumers
        assert expr == "B1"  # expression kept so byte-coverage still maps it
        assert error is None
        # display holds a string *literal* so format_value renders "{raw} (label)".
        assert display == repr("heat_pump")

    def test_enum_unmapped_value_falls_back_to_number(self):
        params = {"MODE": {"expression": "B1", "type": "enum", "values": {0: "off"}}}
        rows = decode_param_rows("62B001", params)
        # 0x62 is unmapped -> _decode_enum returns the numeric string.
        assert rows[0][6] == repr("98")

    def test_bitmask_flags_via_display(self):
        # B1 = 0x62 = 0b1100010 -> bits 1, 5, 6 set.
        params = {
            "FLAGS": {
                "expression": "B1",
                "type": "bitmask",
                "bits": {1: "a", 5: "b", 6: "c"},
            }
        }
        rows = decode_param_rows("62B001", params)
        assert rows[0][6] == repr("a|b|c")

    def test_numeric_alongside_typed_still_decodes(self):
        params = {
            "RAW": {"expression": "B1", "unit": "x"},
            "MODE": {"expression": "B1", "type": "enum", "values": {98: "on"}},
        }
        rows = decode_param_rows("62B001", params)
        assert [r[0] for r in rows] == ["RAW", "MODE"]
        assert rows[0][1] == 0x62 and rows[0][6] == ""
        assert rows[1][6] == repr("on")


class TestDecodeMemoization:
    def test_repeat_payload_served_from_cache(self, monkeypatch):
        import canlib.decoding as dec

        dec._decode_cached.cache_clear()
        calls = {"n": 0}
        real = dec.evaluate_expression

        def counting(expr, data, V=0.0):
            calls["n"] += 1
            return real(expr, data, V)

        monkeypatch.setattr(dec, "evaluate_expression", counting)
        params = {"SOC": {"expression": "B1", "unit": "%"}}
        r1 = dec.decode_param_rows("62B001", params)
        r2 = dec.decode_param_rows("62B001", params)  # identical -> cache hit
        assert r1 == r2
        assert r1 is not r2  # callers get independent list copies
        assert calls["n"] == 1  # evaluate_expression only ran on the first decode

    def test_changed_payload_recomputes(self, monkeypatch):
        import canlib.decoding as dec

        dec._decode_cached.cache_clear()
        calls = {"n": 0}
        real = dec.evaluate_expression

        def counting(expr, data, V=0.0):
            calls["n"] += 1
            return real(expr, data, V)

        monkeypatch.setattr(dec, "evaluate_expression", counting)
        params = {"SOC": {"expression": "B1"}}
        dec.decode_param_rows("62B001", params)
        dec.decode_param_rows("62B099", params)  # different payload -> miss
        assert calls["n"] == 2
