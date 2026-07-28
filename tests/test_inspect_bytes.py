"""Tests for canlib.inspect_bytes: byte interpretation, WiCAN expression
synthesis, series transforms, and float-noise filtering.

These primitives were extracted from ``commands/_decode_plot`` into the neutral
library leaf ``canlib.inspect_bytes`` so the analysis engine (``xanalysis`` /
``frame_series``) imports *down* rather than up from a command helper.
"""

import math
import struct

from canlib import inspect_bytes as ib

U8 = ("u8", 1, "int", False)
I8 = ("i8", 1, "int", True)
U16 = ("u16", 2, "int", False)
I16 = ("i16", 2, "int", True)
U24 = ("u24", 3, "int", False)
U32 = ("u32", 4, "int", False)
F32 = ("f32", 4, "float", True)

FRAME = bytes([0x04, 0x61, 0x01, 0xAB, 0xCD, 0x00])  # PCI, SID, echo, data...


class TestInterpretBytes:
    def test_unsigned_and_signed_byte(self):
        assert ib.interpret_bytes(FRAME, 3, U8) == 171.0
        assert ib.interpret_bytes(FRAME, 3, I8) == -85.0

    def test_endianness(self):
        assert ib.interpret_bytes(FRAME, 3, U16) == 0xABCD  # big-endian
        assert ib.interpret_bytes(FRAME, 3, U16, little=True) == 0xCDAB
        assert ib.interpret_bytes(FRAME, 3, I16) == 0xABCD - 0x10000  # signed BE

    def test_u24_u32(self):
        assert ib.interpret_bytes(FRAME, 3, U24) == 0xABCD00
        assert ib.interpret_bytes(FRAME, 2, U32) == 0x01ABCD00

    def test_out_of_range_returns_none(self):
        assert ib.interpret_bytes(FRAME, 5, U16) is None  # only 1 byte left
        assert ib.interpret_bytes(FRAME, -1, U8) is None

    def test_float_roundtrips(self):
        raw = struct.pack(">f", 3.5)
        assert ib.interpret_bytes(raw, 0, F32) == 3.5

    def test_float_can_be_nan_or_inf(self):
        nan_bytes = bytes([0x7F, 0xC0, 0x00, 0x00])  # IEEE-754 quiet NaN, big-endian
        v = ib.interpret_bytes(nan_bytes, 0, F32)
        assert v is not None and math.isnan(v)
        inf_bytes = bytes([0x7F, 0x80, 0x00, 0x00])  # +Inf
        assert ib.interpret_bytes(inf_bytes, 0, F32) == math.inf


class TestWicanExpr:
    def test_single_byte(self):
        assert ib.wican_expr(3, U8) == "B3"
        assert ib.wican_expr(3, I8) == "S3"

    def test_big_endian_ranges(self):
        assert ib.wican_expr(3, U16) == "[B3:B4]"
        assert ib.wican_expr(3, I16) == "[S3:S4]"
        assert ib.wican_expr(10, U32) == "[B10:B13]"

    def test_little_endian_unsigned_shift(self):
        assert ib.wican_expr(3, U16, little=True) == "B3 | (B4 << 8)"

    def test_inexpressible_cases_return_none(self):
        assert ib.wican_expr(3, I16, little=True) is None  # LE signed
        assert ib.wican_expr(3, F32) is None  # float


class TestTransforms:
    def test_raw_is_identity(self):
        assert ib.apply_transform([1, 2, 3], "raw") == [1, 2, 3]

    def test_delta(self):
        assert ib.apply_transform([1, 3, 6], "delta") == [0.0, 2, 3]

    def test_abs(self):
        assert ib.apply_transform([-1, 2, -3], "abs") == [1, 2, 3]

    def test_cumsum(self):
        assert ib.apply_transform([1, 2, 3], "cumsum") == [1.0, 3.0, 6.0]

    def test_normalize(self):
        assert ib.apply_transform([0, 5, 10], "normalize") == [0.0, 0.5, 1.0]

    def test_smooth_preserves_length(self):
        out = ib.apply_transform([1, 2, 3, 4, 5], "smooth")
        assert len(out) == 5

    def test_empty(self):
        assert ib.apply_transform([], "delta") == []


class TestNorm01:
    def test_normalizes_to_unit_range(self):
        assert ib.norm01([0, 5, 10]) == [0.0, 0.5, 1.0]

    def test_zero_span_maps_to_all_zeros(self):
        assert ib.norm01([7, 7]) == [0.0, 0.0]

    def test_empty(self):
        assert ib.norm01([]) == []


class TestFloatSeriesIsNoise:
    def test_denormal_tiny_values_are_noise(self):
        # Interpreting integer byte runs as f32 yields absurdly tiny magnitudes
        # that must be filtered from the hunt sweep.
        assert ib.float_series_is_noise([5.8e-36, 1.2e-35, 3.4e-36])

    def test_astronomically_large_values_are_noise(self):
        assert ib.float_series_is_noise([1e20, 5e18, 2e19])

    def test_plausible_physical_floats_are_kept(self):
        assert not ib.float_series_is_noise([22.5, 100.0, 0.05, 371.0])

    def test_empty_or_all_nan_is_noise(self):
        assert ib.float_series_is_noise([])
        assert ib.float_series_is_noise([float("nan"), float("nan")])

    def test_zero_alongside_real_values_is_kept(self):
        assert not ib.float_series_is_noise([0.0, 12.0, 0.0, 8.5])
