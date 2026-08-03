"""Tests for canlib.notation — the typed ByteRef model + display notation."""

import pytest

from canlib.byteindex import wican_to_isotp
from canlib.notation import (
    ByteNotation,
    ByteRef,
    ByteSpace,
    relabel_signal,
    subfunction_bytes_for_pid,
)


class TestByteRefConstruction:
    def test_from_wican_roundtrips_to_wican_offset(self):
        # B09 is the first data byte of the first consecutive frame (ISO-TP 6).
        ref = ByteRef.from_wican(9)
        assert ref.space is ByteSpace.ISOTP
        assert ref.offset == 6  # ISO-TP index
        assert ref.wican_offset == 9  # back to WiCAN

    def test_from_wican_rejects_pci_bytes(self):
        # B00/B01 (FF PCI) and B08/B16 (CF PCI) have no ISO-TP position.
        for pci in (0, 1, 8, 16, 24):
            assert wican_to_isotp(pci) is None
            with pytest.raises(ValueError, match="PCI byte"):
                ByteRef.from_wican(pci)

    def test_from_isotp_is_canonical(self):
        ref = ByteRef.from_isotp(6)
        assert ref.offset == 6
        assert ref.wican_offset == 9

    def test_raw_can_has_no_wican_view(self):
        ref = ByteRef.from_raw_can(3)
        assert ref.space is ByteSpace.RAW_CAN
        with pytest.raises(ValueError):
            _ = ref.wican_offset
        assert ref.to_wican_expression() is None


class TestToWicanExpression:
    def test_single_unsigned_byte(self):
        assert ByteRef.from_wican(9).to_wican_expression() == "B9"

    def test_single_signed_byte(self):
        assert ByteRef.from_wican(9, signed=True).to_wican_expression() == "S9"

    def test_bit(self):
        assert ByteRef.from_wican(14, bit=5).to_wican_expression() == "B14:5"

    def test_contiguous_big_endian_range(self):
        # WiCAN B04,B05 are contiguous (ISO-TP 2,3) -> range form.
        ref = ByteRef.from_wican(4, width=2)
        assert ref.to_wican_expression() == "[B4:B5]"

    def test_contiguous_signed_range(self):
        ref = ByteRef.from_wican(4, width=2, signed=True)
        assert ref.to_wican_expression() == "[S4:S5]"

    def test_signed_widths_the_range_form_cannot_sign_extend(self):
        """3/5/6/7-byte signed reads must not use `[Sn:Sm]`.

        The firmware sign-extends a signed range from the top bit of the native
        container it accumulates into (int8/16/32/64), so only 1/2/4/8-byte spans
        are exact. A 3-byte `[S4:S6]` reads bit 31 as the sign bit and can never
        be negative — see canlib.expression.signed_range_is_exact.
        """
        from canlib.expression import evaluate_expression

        for width in (3, 5, 6, 7):
            ref = ByteRef.from_wican(2, width=width, signed=True)
            expr = ref.to_wican_expression()
            assert expr is not None
            assert "[S" not in expr, f"width {width} must not use the range form: {expr}"

        # An all-0xFF..FE 3-byte read is -2, not 16777214.
        frame = bytes([0, 0, 0xFF, 0xFF, 0xFE] + [0] * 8)
        expr = ByteRef.from_wican(2, width=3, signed=True).to_wican_expression()
        assert expr is not None
        assert evaluate_expression(expr, frame) == -2.0

    def test_unsigned_ranges_are_unaffected_by_the_signed_width_rule(self):
        # `[Bn:Bm]` has no sign bit, so every width keeps the compact range form
        # — provided the bytes don't straddle a PCI byte (B8 here), which forces
        # a shift composition for an unrelated reason.
        for width in (3, 5, 6):
            expr = ByteRef.from_wican(2, width=width).to_wican_expression()
            assert expr == f"[B2:B{2 + width - 1}]"

    def test_little_endian_unsigned_shift_composition(self):
        # LE unsigned -> shift form (matches inspect_bytes.wican_expr).
        ref = ByteRef.from_wican(4, width=2, little=True)
        assert ref.to_wican_expression() == "B4 | (B5 << 8)"

    def test_little_endian_signed_arithmetic_composition(self):
        # LE signed -> arithmetic form with the MSB signed (never <</| on a
        # negative high byte). Promotable, unlike the old None/<no-expr>.
        ref = ByteRef.from_wican(4, width=2, signed=True, little=True)
        assert ref.to_wican_expression() == "B4 + S5*256"

    def test_pci_straddling_signed_arithmetic_composition(self):
        # BE signed straddling a PCI byte (ISO-TP 5,6 -> WiCAN B07,B09): the
        # range form is impossible, so compose arithmetically with the MSB signed.
        ref = ByteRef.from_isotp(5, width=2, signed=True)
        assert ref.to_wican_expression() == "S7*256 + B9"

    def test_pci_straddling_range_uses_shift_composition(self):
        # ISO-TP bytes 5 and 6 are WiCAN B07 and B09 (B08 is a CF PCI byte).
        # Contiguous in ISO-TP, NOT in WiCAN -> must shift-compose, never [B07:B09].
        ref = ByteRef.from_isotp(5, width=2)
        assert ByteRef.from_isotp(5).wican_offset == 7
        assert ByteRef.from_isotp(6).wican_offset == 9
        expr = ref.to_wican_expression()
        assert expr == "(B7 << 8) | B9"
        assert "[" not in expr  # never a PCI-spanning range

    def test_matches_decode_plot_wican_expr_for_common_cases(self):
        # Parity with the shared inspector expression generator.
        from canlib.inspect_bytes import INSPECT_TYPES, wican_expr

        by_name = {spec[0]: spec for spec in INSPECT_TYPES}
        for name, width, signed, little in [
            ("u8", 1, False, False),
            ("i8", 1, True, False),
            ("u16", 2, False, False),
            ("i16", 2, True, False),
            ("u16", 2, False, True),  # LE unsigned
            ("i16", 2, True, True),  # LE signed -> arithmetic form
            ("i24", 3, True, True),
        ]:
            spec = by_name[name]
            # Use a WiCAN offset whose window stays within one contiguous frame.
            ref = ByteRef.from_wican(9, width=width, signed=signed, little=little)
            assert ref.to_wican_expression() == wican_expr(9, spec, little=little), (
                name,
                little,
            )


class TestRender:
    def test_wican_default_matches_expression(self):
        assert ByteRef.from_wican(9).render(ByteNotation.WICAN) == "B9"
        assert ByteRef.from_wican(14, bit=5).render(ByteNotation.WICAN) == "B14:5"

    def test_isotp_view(self):
        assert ByteRef.from_wican(9).render(ByteNotation.ISOTP) == "i6"
        assert ByteRef.from_wican(14, bit=5).render(ByteNotation.ISOTP) == "i11.5"

    def test_torque_view_sub1_vs_sub2(self):
        # B09 -> ISO-TP 6. Torque 1 (sub=1) skips SID+1 => byte 4 => "E".
        assert ByteRef.from_wican(9).render(ByteNotation.TORQUE, sub_bytes=1) == "E"
        # sub=2 skips SID+2 => byte 3 => "D".
        assert ByteRef.from_wican(9).render(ByteNotation.TORQUE, sub_bytes=2) == "D"

    def test_bix_view_includes_bit(self):
        # ISO-TP 6, sub=1 -> Torque byte 4 -> bix 32; bit 3 -> 35.
        assert ByteRef.from_wican(9).render(ByteNotation.BIX, sub_bytes=1) == "32"
        assert ByteRef.from_wican(9, bit=3).render(ByteNotation.BIX, sub_bytes=1) == "35"

    def test_torque_dash_for_header_byte(self):
        # WiCAN B02 = SID (ISO-TP 0): no Torque index.
        assert ByteRef.from_wican(2).render(ByteNotation.TORQUE) == "—"
        assert ByteRef.from_wican(2).render(ByteNotation.BIX) == "—"

    def test_raw_can_render(self):
        assert ByteRef.from_raw_can(3).render(ByteNotation.WICAN) == "r3"
        assert ByteRef.from_raw_can(3, bit=2).render(ByteNotation.ISOTP) == "r3.2"


class TestRelabelSignal:
    def test_wican_is_noop(self):
        assert relabel_signal("BMS:2101:B9", ByteNotation.WICAN) == "BMS:2101:B9"

    def test_relabels_byte_with_prefix(self):
        assert relabel_signal("BMS:2101:B9", ByteNotation.ISOTP) == "BMS:2101:i6"

    def test_relabels_bit(self):
        assert relabel_signal("BMS:2101:B14:5", ByteNotation.ISOTP) == "BMS:2101:i11.5"

    def test_relabels_bare_byte(self):
        assert relabel_signal("B9", ByteNotation.TORQUE, sub_bytes=1) == "E"

    def test_named_param_unchanged(self):
        assert relabel_signal("BMS:2101:SOC_BMS", ByteNotation.ISOTP) == "BMS:2101:SOC_BMS"
        assert relabel_signal("ESC:22C101:REAL_SPEED_KMH", ByteNotation.BIX) == (
            "ESC:22C101:REAL_SPEED_KMH"
        )

    def test_pci_byte_label_left_unchanged(self):
        # B08 is a PCI byte -> from_wican raises -> label passes through.
        assert relabel_signal("X:Y:B8", ByteNotation.ISOTP) == "X:Y:B8"

    def test_auto_derives_sub_bytes_from_pid(self):
        # Torque view depends on subfunction width, taken from the label's PID.
        # ISO-TP 6: sub=1 (21xx) -> "E"; sub=2 (22xxxx) -> "D".
        assert relabel_signal("BMS:2101:B9", ByteNotation.TORQUE) == "BMS:2101:E"
        assert relabel_signal("ESC:22C101:B9", ByteNotation.TORQUE) == "ESC:22C101:D"


class TestResolveNotation:
    def test_explicit_value_wins(self):
        from canlib.notation import resolve_notation

        assert resolve_notation("isotp") is ByteNotation.ISOTP
        assert resolve_notation("torque") is ByteNotation.TORQUE

    def test_defaults_to_wican(self, monkeypatch):
        from canlib import notation

        monkeypatch.setattr(notation, "get_config_key", lambda k: None, raising=False)
        # No flag, no config -> wican.
        import canlib.config as cfg

        monkeypatch.setattr(cfg, "get_config_key", lambda k: None)
        assert notation.resolve_notation(None) is ByteNotation.WICAN

    def test_config_default_used_when_no_flag(self, monkeypatch):
        import canlib.config as cfg

        monkeypatch.setattr(
            cfg, "get_config_key", lambda k: "isotp" if k == "display.byte_notation" else None
        )
        from canlib.notation import resolve_notation

        assert resolve_notation(None) is ByteNotation.ISOTP

    def test_invalid_config_falls_back_to_wican(self, monkeypatch):
        import canlib.config as cfg

        monkeypatch.setattr(cfg, "get_config_key", lambda k: "bogus")
        from canlib.notation import resolve_notation

        assert resolve_notation(None) is ByteNotation.WICAN


class TestSubfunctionBytesForPid:
    def test_service_21_is_one(self):
        assert subfunction_bytes_for_pid("2101") == 1

    def test_service_22_is_two(self):
        assert subfunction_bytes_for_pid("22BC03") == 2
        assert subfunction_bytes_for_pid("22c101") == 2


class TestCommandsAcceptNotationFlag:
    """Every analysis command exposes --notation (default None -> resolve to wican)."""

    def test_all_analysis_commands_accept_notation(self):
        import argparse

        from canlib.commands import correlate, coverage, decode, hunt, investigate

        # Minimal required positionals/flags per command to reach a parseable state.
        # correlate/hunt/investigate are uds/can groups — the notation flag lives on each kind.
        cases = [
            (hunt, ["uds", "MCU", "2102", "--against", "X:Y:Z", "--notation", "isotp"]),
            (correlate, ["uds", "--notation", "torque"]),
            (investigate, ["uds", "BCM", "22B003", "--notation", "bix"]),
            (coverage, ["--notation", "isotp"]),
            (decode, ["BMS", "2101", "--notation", "isotp"]),
        ]
        for module, argv in cases:
            p = module.add_parser(argparse.ArgumentParser().add_subparsers())
            args = p.parse_args(argv)
            assert args.notation == argv[argv.index("--notation") + 1]

    def test_notation_defaults_to_none(self):
        import argparse

        from canlib.commands import coverage

        p = coverage.add_parser(argparse.ArgumentParser().add_subparsers())
        assert p.parse_args([]).notation is None

    def test_invalid_notation_rejected(self):
        import argparse

        import pytest

        from canlib.commands import coverage

        p = coverage.add_parser(argparse.ArgumentParser().add_subparsers())
        with pytest.raises(SystemExit):
            p.parse_args(["--notation", "bogus"])
