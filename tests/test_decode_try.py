"""Tests for decode.py --try candidate-expression parsing."""

import importlib.util
from pathlib import Path

import pytest

_SPEC = importlib.util.spec_from_file_location(
    "decode_script", Path(__file__).resolve().parent.parent / "decode.py"
)
decode_script = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(decode_script)

parse_try_expr = decode_script.parse_try_expr
build_try_params = decode_script.build_try_params


class TestParseTryExpr:
    def test_name_and_expression(self):
        assert parse_try_expr("SOC=B9/2") == ("SOC", "", "B9/2")

    def test_name_unit_expression(self):
        assert parse_try_expr("TORQUE:Nm=[S12:S13]/100") == ("TORQUE", "Nm", "[S12:S13]/100")

    def test_expression_may_contain_colons(self):
        # Split is on the first '=', so ranges like [S10:S11] survive intact.
        assert parse_try_expr("RPM=[S10:S11]") == ("RPM", "", "[S10:S11]")

    def test_whitespace_is_trimmed(self):
        assert parse_try_expr("  X : °C = B20 - 40 ") == ("X", "°C", "B20 - 40")

    @pytest.mark.parametrize("bad", ["no_equals", "=B9", "NAME=", ":unit=B9", "   =   "])
    def test_invalid_raises(self, bad):
        with pytest.raises(ValueError):
            parse_try_expr(bad)


class TestBuildTryParams:
    def test_builds_candidate_params(self):
        params = build_try_params(["A=B9", "B:V=[S10:S11]/10"])
        assert set(params) == {"A", "B"}
        assert params["A"] == {"expression": "B9", "unit": "", "verified": False, "candidate": True}
        assert params["B"]["unit"] == "V"
        assert params["B"]["candidate"] is True

    def test_later_duplicate_name_wins(self):
        params = build_try_params(["A=B9", "A=B10"])
        assert params["A"]["expression"] == "B10"

    def test_candidates_decode_against_a_frame(self):
        # End-to-end: a candidate expression evaluates through the real decode path.
        # payload_to_wican_bytes inserts the ISO-TP PCI byte, so 6101ABCD becomes
        # frame 04 61 01 AB CD -> B0=PCI, B1=SID, B2=PID echo, B3=first data byte.
        params = build_try_params(["FIRST=B3"])
        wican = decode_script.payload_to_wican_bytes("6101ABCD")
        decoded = decode_script.decode_payload(wican, params)
        assert decoded["FIRST"]["value"] == 0xAB
        assert decoded["FIRST"]["verified"] is False


def _results(*value_seq):
    """Build fake all_results with one param 'P' taking the given values."""
    out = []
    for v in value_seq:
        decoded = {"P": {"value": v, "unit": "", "verified": False}}
        if v is None:
            decoded["P"]["error"] = "boom"
        out.append({"capture": {}, "decoded": decoded})
    return out


class TestValueRanges:
    def test_min_max_rendered(self, capsys):
        decode_script.print_value_ranges(
            _results(1, 5, 3), ["P"], {"P": {"unit": "", "verified": False}}, set()
        )
        out = capsys.readouterr().out
        assert "1" in out and "5" in out and "—" in out

    def test_constant_marked(self, capsys):
        decode_script.print_value_ranges(
            _results(7, 7), ["P"], {"P": {"unit": "", "verified": False}}, set()
        )
        assert "(constant)" in capsys.readouterr().out

    def test_all_errored_surfaces_error(self, capsys):
        # A param that only ever errors must NOT be silently hidden.
        decode_script.print_value_ranges(
            _results(None, None), ["P"], {"P": {"unit": "", "verified": False}}, set()
        )
        assert "ERROR: boom" in capsys.readouterr().out

    def test_candidate_marker(self, capsys):
        decode_script.print_value_ranges(
            _results(1, 2), ["P"], {"P": {"unit": "", "verified": False}}, {"P"}
        )
        assert "(try)" in capsys.readouterr().out


class TestStatistics:
    def test_mean_median_stdev(self):
        assert decode_script._mean([1, 2, 3, 4]) == 2.5
        assert decode_script._median([1, 2, 3, 4]) == 2.5
        assert decode_script._median([1, 2, 3]) == 2
        assert decode_script._stdev([2, 2, 2]) == 0.0
        assert decode_script._stdev([1]) == 0.0
        assert round(decode_script._stdev([1, 2, 3, 4, 5]), 4) == 1.5811

    def test_pearson_perfect_positive_and_negative(self):
        assert round(decode_script._pearson([1, 2, 3], [2, 4, 6]), 6) == 1.0
        assert round(decode_script._pearson([1, 2, 3], [6, 4, 2]), 6) == -1.0

    def test_pearson_undefined_cases(self):
        assert decode_script._pearson([1], [1]) is None       # too few points
        assert decode_script._pearson([2, 2, 2], [1, 2, 3]) is None  # zero variance

    def test_compute_stats(self):
        s = decode_script.compute_stats([1, 1, 2, 3])
        assert s["n"] == 4 and s["distinct"] == 3
        assert s["min"] == 1 and s["max"] == 3
        assert s["values"] == [1, 2, 3]

    def test_resolve_ref_case_insensitive(self):
        assert decode_script.resolve_ref("soc_bms", ["SOC_BMS", "X"]) == "SOC_BMS"
        assert decode_script.resolve_ref("nope", ["SOC_BMS"]) is None

    def test_series_and_paired_skip_missing(self):
        results = [
            {"decoded": {"A": {"value": 1.0}, "B": {"value": 10.0}}},
            {"decoded": {"A": {"value": 2.0}}},              # B missing
            {"decoded": {"A": {"value": None, "error": "x"}, "B": {"value": 30.0}}},  # A None
        ]
        assert decode_script._series(results, "A") == [1.0, 2.0]
        xs, ys = decode_script._paired(results, "A", "B")
        assert xs == [1.0] and ys == [10.0]   # only the first row has both
