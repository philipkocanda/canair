"""Tests for the consolidated correlation statistics (canlib.stats)."""

import math

import pytest

from canlib.stats import correlation, pearson, rank, spearman


class TestPearson:
    def test_perfect(self):
        assert pearson([1, 2, 3], [2, 4, 6]) == pytest.approx(1.0)
        assert pearson([1, 2, 3], [6, 4, 2]) == pytest.approx(-1.0)

    def test_degenerate(self):
        assert pearson([1], [1]) is None
        assert pearson([2, 2, 2], [1, 2, 3]) is None  # zero variance

    def test_non_finite_is_none(self):
        # f64/f32 byte interpretations can produce inf/nan — must not raise.
        assert pearson([1.0, 2.0, math.inf], [1.0, 2.0, 3.0]) is None
        assert pearson([1.0, 2.0, 3.0], [1.0, math.nan, 3.0]) is None

    def test_huge_values_no_overflow(self):
        # Reading raw bytes as f64 yields values near the float max; squaring
        # their deviations overflows a naive sum. Must return a value, not raise.
        xs = [1e308, 2e307, 1.5e308, 5e307]
        ys = [1.0, 2.0, 3.0, 4.0]
        r = pearson(xs, ys)
        assert r is None or math.isfinite(r)


class TestRank:
    def test_simple(self):
        assert rank([10, 30, 20]) == [1.0, 3.0, 2.0]

    def test_ties_averaged(self):
        # two tied values share the average of ranks 2 and 3 -> 2.5
        assert rank([10, 20, 20, 40]) == [1.0, 2.5, 2.5, 4.0]


class TestSpearman:
    def test_monotone_nonlinear_beats_pearson(self):
        # y = x**2 on x>0 is monotone but not linear: spearman ~1, pearson < 1
        xs = [1.0, 2.0, 3.0, 4.0, 5.0]
        ys = [x * x for x in xs]
        assert spearman(xs, ys) == pytest.approx(1.0)
        assert pearson(xs, ys) < 0.99

    def test_perfect_negative_monotone(self):
        xs = [1, 2, 3, 4]
        ys = [100, 40, 12, 1]  # strictly decreasing
        assert spearman(xs, ys) == pytest.approx(-1.0)

    def test_all_tied_is_none(self):
        assert spearman([1, 2, 3], [5, 5, 5]) is None


class TestCorrelationDispatch:
    def test_dispatch(self):
        xs = [1.0, 2.0, 3.0, 4.0, 5.0]
        ys = [x * x for x in xs]
        assert correlation(xs, ys, "pearson") == pytest.approx(pearson(xs, ys))
        assert correlation(xs, ys, "spearman") == pytest.approx(spearman(xs, ys))
        assert correlation(xs, ys) == pytest.approx(pearson(xs, ys))  # default


class TestDescriptiveStats:
    def test_mean_median_stdev(self):
        from canlib.stats import mean, median, stdev

        assert mean([1, 2, 3, 4]) == 2.5
        assert mean([]) == 0.0  # empty guard
        assert median([1, 2, 3, 4]) == 2.5
        assert median([1, 2, 3]) == 2
        assert stdev([2, 2, 2]) == 0.0
        assert stdev([1]) == 0.0
        assert round(stdev([1, 2, 3, 4, 5]), 4) == 1.5811

    def test_compute_stats(self):
        from canlib.stats import compute_stats

        s = compute_stats([1, 1, 2, 3])
        assert s["n"] == 4 and s["distinct"] == 3
        assert s["min"] == 1 and s["max"] == 3
        assert s["values"] == [1, 2, 3]

    def test_fmt_num(self):
        from canlib.stats import fmt_num

        assert fmt_num(3.0) == "3"
        assert fmt_num(3.5) == "3.50"
        assert fmt_num(float("nan")) == "nan"
        assert fmt_num(float("inf")) == "inf"
        assert fmt_num(float("-inf")) == "-inf"
