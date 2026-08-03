"""Tests for canlib.inspect_bytes: byte interpretation, WiCAN expression
synthesis, series transforms, and float-noise filtering.

These primitives were extracted from ``commands/_decode_plot`` into the neutral
library leaf ``canlib.inspect_bytes`` so the analysis engine (``xanalysis`` /
``frame_series``) imports *down* rather than up from a command helper.
"""

import math
import struct

from canlib import inspect_bytes as ib
from canlib.inspect_bytes import InspectType

U8 = InspectType("u8", 1, "int", False)
I8 = InspectType("i8", 1, "int", True)
U16 = InspectType("u16", 2, "int", False)
I16 = InspectType("i16", 2, "int", True)
U24 = InspectType("u24", 3, "int", False)
I24 = InspectType("i24", 3, "int", True)
U32 = InspectType("u32", 4, "int", False)
I64 = InspectType("i64", 8, "int", True)
F32 = InspectType("f32", 4, "float", True)

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

    def test_big_endian_signed_i24_avoids_the_range_form(self):
        # Regression: `[Snn:Smm]` sign-extends by the *container* width the
        # firmware accumulates into (int8/16/32/64), so a 3-byte span is
        # sign-extended from bit 31 and can never go negative. Emitting it here
        # made `hunt --promote` write an expression that decoded to a different
        # value than the one it correlated on. Must use the arithmetic form.
        assert ib.wican_expr(5, I24) == "S5*65536 + B6*256 + B7"
        assert "[S" not in (ib.wican_expr(5, I24) or "")

    def test_big_endian_signed_i24_expr_matches_interpretation(self):
        from canlib.expression import evaluate_expression

        # 0xFFFFFE as a 24-bit signed value is -2; the old `[S5:S7]` form
        # evaluated to 16777214.
        frame = bytes([0] * 5 + [0xFF, 0xFF, 0xFE] + [0] * 4)
        expr = ib.wican_expr(5, I24)
        assert ib.interpret_bytes(frame, 5, I24) == -2.0
        assert evaluate_expression(expr, frame) == -2.0

    def test_every_int_interpretation_round_trips_through_its_expression(self):
        """Each emitted expression must decode to the value it was derived from.

        The i24 bug hid because nothing checked expression output against
        `interpret_bytes` across the whole type/offset/endianness matrix — the
        exact promise `--promote` relies on.

        Reads up to 4 bytes must round-trip *exactly*. An 8-byte little-endian
        signed read is the one unavoidable exception: it cannot use the exact
        `[Sn:Sm]` range form (wrong byte order), so it composes arithmetically
        through a term of 2**56, and both this evaluator and the firmware
        accumulate in a double — whose ULP up there is 16. That read is therefore
        only accurate to the float64 ULP of its largest term, on-device included.
        """
        import math

        from canlib.expression import evaluate_expression

        frame = bytes([0xFF, 0x80, 0x7F, 0x01, 0xFE, 0xFF, 0xFF, 0xFE, 0x00, 0x81] * 3)
        for spec in ib.INSPECT_TYPES:
            if spec.kind == "float":
                continue
            for little in (False, True):
                for off in range(0, len(frame) - spec.width + 1):
                    expr = ib.wican_expr(off, spec, little=little)
                    assert expr is not None
                    expected = ib.interpret_bytes(frame, off, spec, little=little)
                    got = evaluate_expression(expr, frame)
                    assert expected is not None
                    if "*" in expr and spec.width > 4:
                        # Arithmetic composition through terms above 2**53.
                        tol = math.ulp(2.0 ** (8 * (spec.width - 1))) * spec.width
                    else:
                        tol = 0.0
                    assert abs(got - expected) <= tol, (
                        f"{spec.name} little={little} off={off} expr={expr!r}: {got} != {expected}"
                    )

    def test_big_endian_signed_8_byte_keeps_the_exact_range_form(self):
        # 8 *is* a native container width, so `[Sn:Sm]` is both correct and
        # exact-integer — never downgrade it to a lossy arithmetic composition.
        assert ib.wican_expr(2, I64) == "[S2:S9]"

    def test_little_endian_unsigned_shift(self):
        assert ib.wican_expr(3, U16, little=True) == "B3 | (B4 << 8)"

    def test_little_endian_signed_arithmetic(self):
        # LE signed is expressible arithmetically (MSB signed) — promotable,
        # verified to evaluate identically to interpret_bytes below.
        assert ib.wican_expr(3, I16, little=True) == "B3 + S4*256"
        assert ib.wican_expr(3, I24, little=True) == "B3 + B4*256 + S5*65536"

    def test_little_endian_signed_expr_matches_interpretation(self):
        from canlib.expression import evaluate_expression

        frame = bytes([0] * 3 + [0x30, 0xFF] + [0] * 6)  # B3=0x30, B4=0xFF
        expr = ib.wican_expr(3, I16, little=True)
        assert evaluate_expression(expr, frame) == ib.interpret_bytes(frame, 3, I16, little=True)

    def test_inexpressible_cases_return_none(self):
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


class TestReadIndices:
    # WiCAN frame: B15 (last data byte of frame 1) = 0xFF, B16 = CF PCI (0x21),
    # B17 (first data byte of frame 2) = 0xB1 — the BMS pack-current layout.
    FR = bytes([0] * 15 + [0xFF, 0x21, 0xB1])

    def test_pci_skip_signed(self):
        # (S15<<8)|B17 skipping the PCI byte at B16 → 0xFFB1 signed = -79.
        assert ib.read_indices(self.FR, [15, 17], signed=True) == -79.0

    def test_pci_skip_unsigned(self):
        assert ib.read_indices(self.FR, [15, 17], signed=False) == float(0xFFB1)

    def test_out_of_range_returns_none(self):
        assert ib.read_indices(self.FR, [15, 99], signed=False) is None

    def test_empty_returns_none(self):
        assert ib.read_indices(self.FR, [], signed=False) is None


class TestWicanExprIndices:
    def test_signed_msb_arithmetic_form(self):
        # Arithmetic (not [Snn:Smm]) so a negative MSB and the PCI gap are correct.
        assert ib.wican_expr_indices([15, 17], signed=True) == "S15*256 + B17"

    def test_unsigned(self):
        assert ib.wican_expr_indices([15, 17], signed=False) == "B15*256 + B17"

    def test_three_bytes(self):
        assert ib.wican_expr_indices([14, 15, 17], signed=False) == "B14*65536 + B15*256 + B17"

    def test_expression_evaluates_to_read_indices(self):
        from canlib.expression import evaluate_expression

        fr = TestReadIndices.FR
        expr = ib.wican_expr_indices([15, 17], signed=True)
        assert evaluate_expression(expr, fr) == ib.read_indices(fr, [15, 17], signed=True)
