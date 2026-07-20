"""Tests for decode.py --plot helpers: byte interpretation, transforms, rendering."""

import importlib.util
from pathlib import Path

_SPEC = importlib.util.spec_from_file_location(
    "decode_plot", Path(__file__).resolve().parent.parent / "decode.py"
)
dp = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(dp)

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
        assert dp.interpret_bytes(FRAME, 3, U8) == 171.0
        assert dp.interpret_bytes(FRAME, 3, I8) == -85.0

    def test_endianness(self):
        assert dp.interpret_bytes(FRAME, 3, U16) == 0xABCD          # big-endian
        assert dp.interpret_bytes(FRAME, 3, U16, little=True) == 0xCDAB
        assert dp.interpret_bytes(FRAME, 3, I16) == 0xABCD - 0x10000  # signed BE

    def test_u24_u32(self):
        assert dp.interpret_bytes(FRAME, 3, U24) == 0xABCD00
        assert dp.interpret_bytes(FRAME, 2, U32) == 0x01ABCD00

    def test_out_of_range_returns_none(self):
        assert dp.interpret_bytes(FRAME, 5, U16) is None   # only 1 byte left
        assert dp.interpret_bytes(FRAME, -1, U8) is None

    def test_float_roundtrips(self):
        import struct
        raw = struct.pack(">f", 3.5)
        assert dp.interpret_bytes(raw, 0, F32) == 3.5


class TestWicanExpr:
    def test_single_byte(self):
        assert dp.wican_expr(3, U8) == "B3"
        assert dp.wican_expr(3, I8) == "S3"

    def test_big_endian_ranges(self):
        assert dp.wican_expr(3, U16) == "[B3:B4]"
        assert dp.wican_expr(3, I16) == "[S3:S4]"
        assert dp.wican_expr(10, U32) == "[B10:B13]"

    def test_little_endian_unsigned_shift(self):
        assert dp.wican_expr(3, U16, little=True) == "B3 | (B4 << 8)"

    def test_inexpressible_cases_return_none(self):
        assert dp.wican_expr(3, I16, little=True) is None   # LE signed
        assert dp.wican_expr(3, F32) is None                # float


class TestTransforms:
    def test_raw_is_identity(self):
        assert dp.apply_transform([1, 2, 3], "raw") == [1, 2, 3]

    def test_delta(self):
        assert dp.apply_transform([1, 3, 6], "delta") == [0.0, 2, 3]

    def test_abs(self):
        assert dp.apply_transform([-1, 2, -3], "abs") == [1, 2, 3]

    def test_cumsum(self):
        assert dp.apply_transform([1, 2, 3], "cumsum") == [1.0, 3.0, 6.0]

    def test_normalize(self):
        assert dp.apply_transform([0, 5, 10], "normalize") == [0.0, 0.5, 1.0]

    def test_smooth_preserves_length(self):
        out = dp.apply_transform([1, 2, 3, 4, 5], "smooth")
        assert len(out) == 5

    def test_empty(self):
        assert dp.apply_transform([], "delta") == []


class TestRendering:
    def test_norm01(self):
        assert dp._norm01([0, 5, 10]) == [0.0, 0.5, 1.0]
        assert dp._norm01([7, 7]) == [0.0, 0.0]   # zero span -> all 0

    def test_pci_positions(self):
        # 6101ABCD -> frame 04 61 01 AB CD; only B0 is PCI.
        assert dp._pci_positions("6101ABCD") == {0}

    def test_render_plot_shape(self):
        lines = dp.render_plot([1, 2, 3, 2, 1], width=20, height=6)
        assert len(lines) == 8            # height + axis + caption
        assert any("\u2802" <= ch <= "\u28ff" for ch in "".join(lines))  # has braille

    def test_render_plot_empty(self):
        assert dp.render_plot([]) == ["  (no data to plot)"]

    def test_overlay_normalizes(self):
        # With a ref, both series are normalized; caption notes it.
        lines = dp.render_plot([10, 20, 30], ref=[1, 2, 3], width=20, height=6)
        assert any("normalized" in ln for ln in lines)
