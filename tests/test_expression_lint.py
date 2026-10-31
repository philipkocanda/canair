"""Tests for the structural expression lints (canlib.expression.lint)."""

from pathlib import Path

import pytest
import yaml

from canlib.commands import validate as validate_pids
from canlib.expression import evaluate_expression
from canlib.expression.lint import firmware_grammar_problems, signed_low_bytes


class TestFlagsSignedLowByte:
    @pytest.mark.parametrize(
        ("expr", "low", "top"),
        [
            # The BMS 2101 pack-current bug (e809c51), and its power product.
            ("((S15 << 8) | S17)/10", "S17", "S15"),
            ("(([B18:B19]/10) * (((S15 << 8) | S17)/10)) / 1000", "S17", "S15"),
            ("S3*256 + S4", "S4", "S3"),
            ("256*S3 + S4", "S4", "S3"),
            ("S4 + (B3 << 8)", "S4", "B3"),
            ("(B2 << 16) | (S3 << 8) | B4", "S3", "B2"),
            ("([S2:S3] << 16) | [S4:S5]", "[S4:S5]", "[S2:S3]"),
        ],
    )
    def test_flags(self, expr, low, top):
        assert signed_low_bytes(expr) == [(low, top)]

    def test_flags_every_signed_low_byte(self):
        assert signed_low_bytes("(S1 << 16) | (S2 << 8) | S3") == [("S2", "S1"), ("S3", "S1")]


class TestPassesCorrectForms:
    @pytest.mark.parametrize(
        "expr",
        [
            "((S15 << 8) | B17)/10",  # the fix
            "S3*256 + B4",
            "[S12:S13]/100",
            "S10/2 - 40",
            "S3 + S4",  # a sum of two signed bytes, not a word
            "S1*2 + S2",  # not a byte-aligned weight
            "(B1 << 4) | S2",  # nibble packing, not a byte word
            "(S1*100) + S2",
            "B17:0 + S3",
            "V * S3",
        ],
    )
    def test_passes(self, expr):
        assert signed_low_bytes(expr) == []

    def test_unparseable_is_left_to_the_syntax_check(self):
        assert signed_low_bytes("((S1 << 8) |") == []


def test_parse_shares_the_evaluator_grammar():
    # The bug the lint targets is real under the evaluator's semantics: +15.0 A
    # (0x0096) read with a signed low byte comes out 25.6 A low.
    data = bytes(15) + bytes([0x00, 0x21, 0x96])
    assert evaluate_expression("((S15 << 8) | B17)/10", data) == pytest.approx(15.0)
    assert evaluate_expression("((S15 << 8) | S17)/10", data) == pytest.approx(-10.6)


def test_validate_reports_it_as_an_error_with_the_fix():
    (msg,) = validate_pids.validate_expression("((S15 << 8) | S17)/10", "I", "2101", "BMS")
    assert "BMS/2101/I" in msg and "'S17'" in msg and "use 'B17'" in msg


def test_shipped_profiles_have_no_signed_low_bytes():
    offenders = []
    for path in (Path(__file__).parents[1] / "profiles").glob("*/ecus/*.yaml"):
        data = yaml.safe_load(open(path)) or {}
        for ecu, ecud in data.items():
            for pid, pidd in ((ecud or {}).get("pids") or {}).items():
                for pname, pmeta in ((pidd or {}).get("parameters") or {}).items():
                    expr = (pmeta or {}).get("expression") or ""
                    offenders += [f"{ecu}/{pid}/{pname}: {expr}" for _ in signed_low_bytes(expr)]
    assert not offenders, "signed low bytes:\n" + "\n".join(offenders)


class TestFirmwareGrammar:
    """Forms WiCAN firmware v6's expression checker refuses but canair's evaluator reads.

    The refused cases are the v6 checker's own verdicts (expression_parser_check, run on
    the host against meatpi-components aa4c2b3).
    """

    @pytest.mark.parametrize(
        ("expr", "reason"),
        [
            ("[B3:B0]", "inverted range"),
            ("[S5:S4]", "inverted range"),
            ("[B0:B8]", "more than 8 bytes"),
            ("B4:8", "bit index 8"),
            ("B3 B4", "no single value"),
            ("B3(B4)", "no single value"),
        ],
    )
    def test_refused(self, expr, reason):
        (problem,) = firmware_grammar_problems(expr)
        assert reason in problem
        evaluate_expression(expr, bytes(range(16)))  # while canair decodes it

    @pytest.mark.parametrize(
        "expr", ["[B0:B7]", "[S2:S3]", "B4:7", "(B3<<8)|B4", "B9/2-40", "V*B3", "B03"]
    )
    def test_accepted(self, expr):
        assert firmware_grammar_problems(expr) == []

    def test_unparseable_is_left_to_the_syntax_check(self):
        assert firmware_grammar_problems("((B1 << 8) |") == []

    def test_validate_names_the_signal(self):
        from canlib.commands.validate.pids import firmware_expression_errors

        (msg,) = firmware_expression_errors("[B0:B8]", "X", "2101", "BMS")
        assert "BMS/2101/X" in msg and "firmware v6" in msg


@pytest.mark.parametrize(("status", "refused"), [("active", True), ("draft", False)])
def test_validate_judges_only_what_ships(tmp_path, status, refused):
    from canlib.commands.validate import validate_pids_file

    path = tmp_path / "test.yaml"
    path.write_text(
        "TEST:\n  tx_id: 0x700\n  pids:\n    2101:\n"
        f"      status: {status}\n      parameters:\n        TAG:\n"
        "          expression: '[B4:B12]'\n          verified: true\n"
    )
    ok, out = validate_pids_file(path)
    assert ok is not refused
    assert ("firmware v6" in out) is refused
