"""Tests for decode.py --try candidate-expression parsing."""

import pytest

from canlib.commands import decode as decode_script

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
        from canlib.stats import pearson

        assert round(pearson([1, 2, 3], [2, 4, 6]), 6) == 1.0
        assert round(pearson([1, 2, 3], [6, 4, 2]), 6) == -1.0

    def test_pearson_undefined_cases(self):
        from canlib.stats import pearson

        assert pearson([1], [1]) is None  # too few points
        assert pearson([2, 2, 2], [1, 2, 3]) is None  # zero variance

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
            {"decoded": {"A": {"value": 2.0}}},  # B missing
            {"decoded": {"A": {"value": None, "error": "x"}, "B": {"value": 30.0}}},  # A None
        ]
        assert decode_script._series(results, "A") == [1.0, 2.0]
        xs, ys = decode_script._paired(results, "A", "B")
        assert xs == [1.0] and ys == [10.0]  # only the first row has both

    def test_paired_timed_sorts_by_capture_time(self):
        # Regression: same-PID --corr-transform delta paired in capture-list
        # order; an out-of-order capture would corrupt the rate. _paired_timed
        # must reorder chronologically before the transform.
        results = [
            {
                "capture": {"date": "2026-07-22", "time": "09:00:02"},
                "decoded": {"A": {"value": 2.0}, "B": {"value": 20.0}},
            },
            {
                "capture": {"date": "2026-07-22", "time": "09:00:00"},
                "decoded": {"A": {"value": 1.0}, "B": {"value": 10.0}},
            },
            {
                "capture": {"date": "2026-07-22", "time": "09:00:01"},
                "decoded": {"A": {"value": 3.0}, "B": {"value": 30.0}},
            },
        ]
        xs, ys = decode_script._paired_timed(results, "A", "B")
        assert xs == [1.0, 3.0, 2.0]  # ordered by 09:00:00, :01, :02
        assert ys == [10.0, 30.0, 20.0]

    def test_paired_timed_undated_sorts_last(self):
        results = [
            {
                "capture": {"date": "2026-07-22"},  # untimed -> datetime.max -> last
                "decoded": {"A": {"value": 9.0}, "B": {"value": 90.0}},
            },
            {
                "capture": {"date": "2026-07-22", "time": "09:00:00"},
                "decoded": {"A": {"value": 1.0}, "B": {"value": 10.0}},
            },
        ]
        xs, ys = decode_script._paired_timed(results, "A", "B")
        assert xs == [1.0, 9.0] and ys == [10.0, 90.0]


class TestCrossSignalCorrelation:
    """Tranche 1.1 — cross-ECU/PID --corr via time-aligned nearest-join."""

    def _results_with_time(self):
        # local param B on this PID, timestamped for the cross-join

        return [
            {
                "capture": {"date": "2026-07-22", "time": "09:00:00"},
                "decoded": {"B": {"value": 10.0}},
            },
            {
                "capture": {"date": "2026-07-22", "time": "09:00:02"},
                "decoded": {"B": {"value": 20.0}},
            },
        ]

    def test_local_series_uses_capture_datetime(self):
        from datetime import datetime

        series = decode_script._local_series(self._results_with_time(), "B")
        assert [tp.value for tp in series] == [10.0, 20.0]
        assert series[0].dt == datetime(2026, 7, 22, 9, 0, 0)

    def test_local_series_drops_untimed(self):
        results = [
            {"capture": {"date": "2026-07-22"}, "decoded": {"B": {"value": 5.0}}},  # no time
        ]
        assert decode_script._local_series(results, "B") == []

    def test_print_cross_correlation(self, capsys):
        from datetime import datetime

        from canlib.align import TimePoint

        ref = [
            TimePoint(datetime(2026, 7, 22, 9, 0, 0, 300000), 10.0),
            TimePoint(datetime(2026, 7, 22, 9, 0, 2, 200000), 20.0),
        ]
        decode_script.print_correlations(
            self._results_with_time(),
            ["B"],
            {"B": {"expression": "B4"}},
            set(),
            "EXT:PID:REF",
            cross_ref_series=ref,
            cross_ref_label="EXT:PID:REF",
            tol_s=1.0,
        )
        out = capsys.readouterr().out
        assert "nearest-join" in out
        assert "r=+1.000" in out  # B perfectly tracks the ref
        assert "n=2" in out


class TestDiscriminate:
    """Tranche 2.1 — state-discriminability ranking."""

    def test_discriminability_clean_separation(self):
        # nearly constant within each group, very different across => high F
        groups = {"a": [10.0, 10.1, 9.9], "b": [50.0, 50.1, 49.9]}
        f = decode_script._discriminability(groups)
        assert f is not None and f > 100

    def test_discriminability_noise_low(self):
        groups = {"a": [10.0, 50.0, 30.0], "b": [12.0, 48.0, 31.0]}
        f = decode_script._discriminability(groups)
        assert f is not None and f < 2

    def test_discriminability_single_group_none(self):
        assert decode_script._discriminability({"a": [1.0, 2.0, 3.0]}) is None

    def test_discriminability_perfect_zero_within(self):
        groups = {"a": [10.0, 10.0], "b": [50.0, 50.0]}
        assert decode_script._discriminability(groups) == float("inf")

    def test_print_discriminate(self, capsys):
        results = [
            {"capture": {"vehicle_states": ["charging"]}, "decoded": {"T": {"value": 20.0}}},
            {"capture": {"vehicle_states": ["charging"]}, "decoded": {"T": {"value": 21.0}}},
            {"capture": {"vehicle_states": ["driving"]}, "decoded": {"T": {"value": 90.0}}},
            {"capture": {"vehicle_states": ["driving"]}, "decoded": {"T": {"value": 92.0}}},
        ]
        decode_script.print_discriminate(results, ["T"], {}, set(), "state")
        out = capsys.readouterr().out
        assert "Discriminability by state" in out
        assert "charging=20" in out and "driving=91" in out

    def test_discriminate_bytes_surfaces_state_byte(self, capsys):
        # T1.1: a raw byte that is state-dependent (and near-binary: 2 distinct
        # values) should be ranked WITHOUT any --try. Payload 62 B0 04 <d0> <d1>
        # -> WiCAN B4=d0, B5=d1 (B0=PCI). B4 splits cleanly by state; B5 is noise.
        def cap(state, d0, d1):
            return {
                "capture": {"vehicle_states": [state], "payload": f"62B004{d0:02X}{d1:02X}"},
                "decoded": {},
            }

        results = [
            cap("ready", 0x34, 0x10),
            cap("ready", 0x34, 0x99),
            cap("charging", 0x00, 0x12),
            cap("charging", 0x00, 0x77),
        ]
        decode_script.print_discriminate(results, [], {}, set(), "state", include_bytes=True)
        out = capsys.readouterr().out
        assert "params + bytes" in out
        assert "B4" in out  # the near-binary state byte is surfaced
        # B4 (clean split) must rank above B5 (noise): appears earlier in output
        assert out.index("B4") < out.index("B5")

    def test_discriminate_bytes_skips_pci(self, capsys):
        # B0/B1 are PCI on a multi-frame frame; must never be ranked.
        def cap(state, tail):
            # 16-byte raw payload (multi-frame); vary a data byte + rely on PCI skip
            return {
                "capture": {
                    "vehicle_states": [state],
                    "payload": "6181" + "00" * 13 + f"{tail:02X}",
                },
                "decoded": {},
            }

        results = [cap("ready", 1), cap("ready", 2), cap("charging", 200), cap("charging", 201)]
        decode_script.print_discriminate(results, [], {}, set(), "state", include_bytes=True)
        out = capsys.readouterr().out
        assert " B0 " not in out and " B1 " not in out

    def test_discriminate_bits_surfaces_state_bit(self, capsys):
        # T2.4: a bit that flips cleanly by state ranks; header notes bits.
        def cap(state, d0):
            return {
                "capture": {"vehicle_states": [state], "payload": f"62B004{d0:02X}"},
                "decoded": {},
            }

        # B4 bit0 = 1 in ready, 0 in charging -> clean state split
        results = [
            cap("ready", 0x01),
            cap("ready", 0x01),
            cap("charging", 0x00),
            cap("charging", 0x00),
        ]
        decode_script.print_discriminate(results, [], {}, set(), "state", include_bits=True)
        out = capsys.readouterr().out
        assert "params + bits" in out
        assert "B4:0" in out


class TestCorrTransform:
    """Tranche 2.2 — transform-aware correlation (level vs rate)."""

    def test_transform_series_delta(self):
        from datetime import datetime

        from canlib.align import TimePoint

        s = [TimePoint(datetime(2026, 7, 22, 9, 0, i), float(i * i)) for i in range(4)]
        out = decode_script._transform_series(s, "delta")
        # delta of [0,1,4,9] = [0,1,3,5]
        assert [tp.value for tp in out] == [0.0, 1.0, 3.0, 5.0]
        assert [tp.dt for tp in out] == [tp.dt for tp in s]  # times preserved

    def test_local_corr_with_delta_transform(self, capsys):
        # ref ramps 0,1,2,3 (delta=const-ish); B = delta of ref
        results = [
            {"decoded": {"REF": {"value": 0.0}, "B": {"value": 0.0}}},
            {"decoded": {"REF": {"value": 1.0}, "B": {"value": 1.0}}},
            {"decoded": {"REF": {"value": 3.0}, "B": {"value": 2.0}}},
            {"decoded": {"REF": {"value": 6.0}, "B": {"value": 3.0}}},
        ]
        # delta(REF) = [0,1,2,3] which equals B exactly -> r=+1
        decode_script.print_correlations(results, ["B"], {"B": {}}, set(), "REF", transform="delta")
        out = capsys.readouterr().out
        assert "ref delta" in out
        assert "r=+1.000" in out


class TestFindMirrors:
    """Tranche 2.3 — exact byte/bit mirror detection."""

    def _results(self, *payloads):
        return [{"capture": {"payload": p}} for p in payloads]

    def test_byte_mirror_detected(self):
        # payload 62 B0 04 <d0> <d1> <d2> -> WiCAN B4=d0, B5=d1, B6=d2 (B0=PCI)
        # d0 and d1 always equal; d2 varies independently
        results = self._results("62B0040A0A01", "62B004141405", "62B0040909FF")
        mirrors = decode_script.find_mirrors(results)
        pairs = {(a, b) for a, b, _ in mirrors}
        assert ("B4", "B5") in pairs
        assert ("B4", "B6") not in pairs

    def test_constant_bytes_not_reported(self):
        # d0 constant 0x00 in all -> WiCAN B4 excluded (only varying positions)
        results = self._results("62B00400AA", "62B00400BB", "62B00400CC")
        mirrors = decode_script.find_mirrors(results)
        assert all("B4" not in (a, b) for a, b, _ in mirrors)

    def test_bit_mirror(self):
        # payload 62 B0 04 <d0> -> WiCAN B4=d0; bits 0 and 2 co-vary (0x00/0x05)
        results = self._results("62B00400", "62B00405", "62B00400", "62B00405")
        mirrors = decode_script.find_mirrors(results, bits=True)
        pairs = {(a, b) for a, b, _ in mirrors}
        assert ("B4:0", "B4:2") in pairs

    def test_too_few_captures(self):
        assert decode_script.find_mirrors(self._results("62B00401")) == []
