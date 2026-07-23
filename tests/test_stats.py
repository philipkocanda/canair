"""Tests for the consolidated correlation statistics (canlib.stats)."""

import pytest

from canlib.stats import correlation, pearson, rank, spearman


class TestPearson:
    def test_perfect(self):
        assert pearson([1, 2, 3], [2, 4, 6]) == pytest.approx(1.0)
        assert pearson([1, 2, 3], [6, 4, 2]) == pytest.approx(-1.0)

    def test_degenerate(self):
        assert pearson([1], [1]) is None
        assert pearson([2, 2, 2], [1, 2, 3]) is None  # zero variance


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
