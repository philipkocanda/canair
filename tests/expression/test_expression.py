"""Tests for canlib.expression — WiCAN expression evaluator."""

import pytest

from canlib.expression import evaluate_expression


class TestEvaluateExpression:
    """Test the WiCAN expression evaluator against known PID formulas."""

    def _make_bytes(self, hex_str: str) -> bytes:
        return bytes.fromhex(hex_str)

    def test_simple_byte_read(self):
        # B02 from payload where byte 2 = 0x64 (100)
        data = self._make_bytes("00" * 2 + "64" + "00" * 60)
        result = evaluate_expression("B02", data)
        assert result == 100.0

    def test_division(self):
        # SOC_BMS = B09/2, with B09 = 0xC8 (200) -> 100.0
        data = self._make_bytes("00" * 9 + "C8" + "00" * 50)
        result = evaluate_expression("B09/2", data)
        assert result == 100.0

    def test_multi_byte_unsigned(self):
        # [B12:B13]/100, with bytes 12-13 = 0x01 0xF4 (500) -> 5.0
        data = self._make_bytes("00" * 12 + "01F4" + "00" * 50)
        result = evaluate_expression("[B12:B13]/100", data)
        assert result == 5.0

    def test_signed_byte(self):
        # S18-40 (signed byte - offset), with B18 = 0x50 (80) -> 40
        data = self._make_bytes("00" * 18 + "50" + "00" * 40)
        result = evaluate_expression("S18-40", data)
        assert result == 40.0

    def test_bit_extraction(self):
        # B05:0 (bit 0 of byte 5), with B05 = 0x01 -> 1
        data = self._make_bytes("00" * 5 + "01" + "00" * 50)
        result = evaluate_expression("B05:0", data)
        assert result == 1.0

        # B05:1 (bit 1) -> 0
        result = evaluate_expression("B05:1", data)
        assert result == 0.0

    def test_bit_high(self):
        # B05:7 (bit 7), with B05 = 0x80 -> 1
        data = self._make_bytes("00" * 5 + "80" + "00" * 50)
        result = evaluate_expression("B05:7", data)
        assert result == 1.0

    def test_complex_expression(self):
        # (B03*256+B04)/10 with B03=0x01, B04=0xF4 -> 500/10 = 50.0
        data = self._make_bytes("00" * 3 + "01F4" + "00" * 50)
        result = evaluate_expression("(B03*256+B04)/10", data)
        assert result == 50.0


class TestSignedMultiByte:
    """[Sn:Sm] — big-endian signed multi-byte, sign-extended by span width."""

    def _bytes(self, hex_str: str) -> bytes:
        return bytes.fromhex(hex_str)

    def test_span0_negative(self):
        # [S3:S3] == single signed byte; 0xFF -> -1
        data = self._bytes("00" * 3 + "FF" + "00" * 40)
        assert evaluate_expression("[S3:S3]", data) == -1.0

    def test_span0_positive(self):
        data = self._bytes("00" * 3 + "7F" + "00" * 40)  # 127
        assert evaluate_expression("[S3:S3]", data) == 127.0

    def test_span1_negative(self):
        # 0xFFFF as int16 -> -1
        data = self._bytes("00" * 3 + "FFFF" + "00" * 40)
        assert evaluate_expression("[S3:S4]", data) == -1.0

    def test_span1_boundary(self):
        # 0x8000 is the most-negative int16
        data = self._bytes("00" * 3 + "8000" + "00" * 40)
        assert evaluate_expression("[S3:S4]", data) == -32768.0

    def test_span1_positive(self):
        # 0x7FFF -> 32767
        data = self._bytes("00" * 3 + "7FFF" + "00" * 40)
        assert evaluate_expression("[S3:S4]", data) == 32767.0

    def test_span3_four_byte_negative(self):
        # span 3 (4 bytes) sign-extends as int32; 0xFFFFFFFF -> -1
        data = self._bytes("00" * 3 + "FFFFFFFF" + "00" * 40)
        assert evaluate_expression("[S3:S6]", data) == -1.0

    def test_span3_four_byte_positive(self):
        # 0x00000100 -> 256
        data = self._bytes("00" * 3 + "00000100" + "00" * 40)
        assert evaluate_expression("[S3:S6]", data) == 256.0

    def test_span_ge4_eight_byte_negative(self):
        # span >= 4 (up to 8 bytes) sign-extends as int64; all-FF -> -1
        data = self._bytes("00" * 3 + "FF" * 8 + "00" * 40)
        assert evaluate_expression("[S3:S10]", data) == -1.0


class TestBitwiseAndShift:
    """& | ^ << >> operators."""

    def _bytes(self, hex_str: str) -> bytes:
        return bytes.fromhex(hex_str)

    def test_and(self):
        data = self._bytes("0FF0" + "00" * 40)  # B00=0x0F, B01=0xF0
        assert evaluate_expression("B00 & B01", data) == 0.0

    def test_or(self):
        data = self._bytes("0FF0" + "00" * 40)
        assert evaluate_expression("B00 | B01", data) == 255.0

    def test_xor(self):
        data = self._bytes("0FF0" + "00" * 40)
        assert evaluate_expression("B00 ^ B01", data) == 255.0

    def test_left_shift(self):
        data = self._bytes("0F" + "00" * 40)  # 0x0F << 4 = 0xF0
        assert evaluate_expression("B00 << 4", data) == 240.0

    def test_right_shift(self):
        data = self._bytes("F0" + "00" * 40)  # 0xF0 >> 4 = 0x0F
        assert evaluate_expression("B00 >> 4", data) == 15.0

    def test_bitwise_precedence_and_over_or(self):
        # & (prec 2) binds tighter than | (prec 1): B00 | B01 & B02
        # = B00 | (B01 & B02) = 0x01 | (0xFF & 0x02) = 0x01 | 0x02 = 3
        data = self._bytes("01FF02" + "00" * 40)
        assert evaluate_expression("B00 | B01 & B02", data) == 3.0

    def test_shift_lower_precedence_than_add(self):
        # + (prec 4) binds tighter than << (prec 3): B00 + B01 << B02
        # = (B00 + B01) << B02 = (2 + 3) << 4 = 80
        data = self._bytes("020304" + "00" * 40)
        assert evaluate_expression("B00 + B01 << B02", data) == 80.0


class TestExternalValueParam:
    """V — external value parameter."""

    def _bytes(self, hex_str: str) -> bytes:
        return bytes.fromhex(hex_str)

    def test_v_defaults_to_zero(self):
        data = self._bytes("0F" + "00" * 40)
        assert evaluate_expression("B00 + V", data) == 15.0

    def test_v_supplied(self):
        data = self._bytes("0F" + "00" * 40)
        assert evaluate_expression("B00 + V", data, V=10.0) == 25.0

    def test_v_standalone(self):
        assert evaluate_expression("V", b"\x00" * 8, V=42.0) == 42.0


class TestOperatorPrecedence:
    """Arithmetic precedence and grouping."""

    def _bytes(self, hex_str: str) -> bytes:
        return bytes.fromhex(hex_str)

    def test_multiply_before_add(self):
        # B00 + B01 * B02 = 2 + 3*4 = 14
        data = self._bytes("020304" + "00" * 40)
        assert evaluate_expression("B00 + B01 * B02", data) == 14.0

    def test_parentheses_override(self):
        # (B00 + B01) * B02 = (2+3)*4 = 20
        data = self._bytes("020304" + "00" * 40)
        assert evaluate_expression("(B00 + B01) * B02", data) == 20.0

    def test_subtraction_left_associative(self):
        # B00 - B01 - B02 = 10 - 3 - 2 = 5
        data = self._bytes("0A0302" + "00" * 40)
        assert evaluate_expression("B00 - B01 - B02", data) == 5.0


class TestErrorPaths:
    """Malformed / degenerate expressions raise, they never silently misdecode."""

    def _bytes(self, hex_str: str) -> bytes:
        return bytes.fromhex(hex_str)

    def test_division_by_zero(self):
        data = self._bytes("0A" + "00" * 40)
        with pytest.raises(ZeroDivisionError):
            evaluate_expression("B00/0", data)

    def test_empty_expression(self):
        with pytest.raises(ValueError, match="Empty expression"):
            evaluate_expression("", b"\x00" * 8)

    def test_whitespace_only_expression(self):
        with pytest.raises(ValueError, match="Empty expression"):
            evaluate_expression("   ", b"\x00" * 8)

    def test_invalid_character(self):
        with pytest.raises(ValueError, match="Invalid character"):
            evaluate_expression("@bad", b"\x00" * 8)

    def test_invalid_array_syntax(self):
        with pytest.raises(ValueError, match="Invalid array syntax"):
            evaluate_expression("[B0]", b"\x00" * 8)

    def test_invalid_array_mixed_signedness(self):
        with pytest.raises(ValueError, match="Invalid array syntax"):
            evaluate_expression("[B0:S1]", b"\x00" * 8)
