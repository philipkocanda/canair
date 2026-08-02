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


class TestPartialCorrelation:
    def test_matches_closed_form(self):
        from canlib.stats import partial_correlation, pearson

        # Verify the implementation equals the textbook closed form computed from
        # the three pairwise Pearson coefficients.
        x = [1.0, 2.0, 3.0, 4.0, 5.0, 6.0, 7.0]
        y = [2.0, 1.0, 4.0, 3.0, 6.0, 5.0, 8.0]
        z = [1.0, 3.0, 2.0, 5.0, 4.0, 7.0, 6.0]
        r_xy, r_xz, r_yz = pearson(x, y), pearson(x, z), pearson(y, z)
        expected = (r_xy - r_xz * r_yz) / (((1 - r_xz**2) * (1 - r_yz**2)) ** 0.5)
        assert partial_correlation(x, y, z) == pytest.approx(expected)

    def test_removes_a_pure_confounder(self):
        from canlib.stats import partial_correlation, pearson

        # z drives both x and y; the residuals are orthogonal to z and to each
        # other, so the apparent x-y link is entirely spurious; partial approx 0.
        z = [-3.0, -2.0, -1.0, 0.0, 1.0, 2.0, 3.0]
        a = [5.0, 0.0, -3.0, -4.0, -3.0, 0.0, 5.0]  # even, orthogonal to odd z
        b = [-5.0, 0.0, 3.0, 4.0, 3.0, 0.0, -5.0]  # also even, orthogonal to z
        # Make b orthogonal to a: use a different even shape.
        b = [-1.0, 4.0, -1.0, -4.0, -1.0, 4.0, -1.0]
        x = [zz + aa for zz, aa in zip(z, a, strict=True)]
        y = [zz + bb for zz, bb in zip(z, b, strict=True)]
        assert pearson(x, y) > 0.4  # apparent link through z
        r = partial_correlation(x, y, z)
        assert r is not None
        assert abs(r) < 0.25  # partial well below the apparent link

    def test_preserves_independent_link(self):
        from canlib.stats import partial_correlation

        # x tracks y directly; z is unrelated noise → partial ≈ full correlation.
        x = [1.0, 2.0, 3.0, 4.0, 5.0, 6.0]
        y = [1.1, 1.9, 3.2, 3.9, 5.1, 5.8]
        z = [7.0, 1.0, 9.0, 2.0, 8.0, 3.0]
        r = partial_correlation(x, y, z)
        assert r is not None
        assert r > 0.9

    def test_collinear_control_returns_none(self):
        from canlib.stats import partial_correlation

        x = [1.0, 2.0, 3.0, 4.0]
        y = [2.0, 4.0, 6.0, 8.0]
        z = list(x)  # control identical to reference → denominator 0
        assert partial_correlation(x, y, z) is None

    def test_categorical_method_raises(self):
        from canlib.stats import partial_correlation

        with pytest.raises(ValueError):
            partial_correlation([1, 2], [1, 2], [1, 2], method="cramers_v")


class TestCategoricalMethodNudge:
    def test_nudges_numeric_methods(self):
        from canlib.stats import categorical_method_nudge

        for m in ("pearson", "spearman"):
            nudge = categorical_method_nudge(m)
            assert "cramers_v" in nudge
            assert nudge.startswith(" ")

    def test_silent_for_categorical_methods(self):
        from canlib.stats import categorical_method_nudge

        assert categorical_method_nudge("cramers_v") == ""
        assert categorical_method_nudge("mutual_info") == ""
