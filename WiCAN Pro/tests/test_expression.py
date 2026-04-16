"""Tests for canlib.expression — WiCAN expression evaluator."""

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
