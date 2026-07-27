"""Tests for the byte-triage leaf (canlib.triage)."""

from canlib import triage


class TestByteEntropy:
    def test_constant_is_zero(self):
        assert triage.byte_entropy([5, 5, 5, 5]) == 0.0

    def test_uniform_two_values_is_one_bit(self):
        assert triage.byte_entropy([0, 1, 0, 1]) == 1.0

    def test_random_byte_near_eight_bits(self):
        h = triage.byte_entropy(list(range(256)))
        assert h == 8.0


class TestLag1Autocorr:
    def test_slow_ramp_high(self):
        r = triage.lag1_autocorr([float(i) for i in range(20)])
        assert r is not None and r > 0.9

    def test_alternating_negative(self):
        r = triage.lag1_autocorr([0.0, 1.0] * 10)
        assert r is not None and r < 0

    def test_constant_none(self):
        assert triage.lag1_autocorr([3.0, 3.0, 3.0]) is None

    def test_too_short_none(self):
        assert triage.lag1_autocorr([1.0, 2.0]) is None


class TestStepAndFlip:
    def test_mean_abs_step_counter(self):
        assert triage.mean_abs_step([0.0, 1.0, 2.0, 3.0]) == 1.0

    def test_flip_rate(self):
        assert triage.flip_rate([1, 1, 2, 2, 3]) == 0.5  # 2 changes over 4 transitions

    def test_bit_flip_rates_lsb_toggles(self):
        # +1 counter: LSB flips every step, bit1 every other, etc.
        rates = triage.bit_flip_rates([0, 1, 2, 3, 4, 5, 6, 7])
        assert rates[0] == 1.0  # LSB flips on every increment
        assert rates[1] < rates[0]  # higher bits flip less often


class TestClassify:
    def test_constant(self):
        assert triage.classify([9, 9, 9, 9]) == "constant"

    def test_counter(self):
        assert triage.classify(list(range(256))) == "counter"

    def test_checksum(self):
        # High-entropy but erratic (large jumps) -> checksum, not counter.
        vals = [(i * 167 + 13) % 256 for i in range(256)]
        assert triage.classify(vals) == "checksum"

    def test_enum(self):
        assert triage.classify([1, 2, 3, 1, 2, 3, 1] * 5) == "enum"

    def test_continuous(self):
        # Many distinct values, smooth, small steps, but not near byte-random.
        vals = [40 + (i % 30) for i in range(200)]
        assert triage.classify(vals) == "continuous"


class TestDetectWords:
    def test_finds_constant_hi_wide_lo(self):
        # hi barely moves (integer part), lo sweeps 0..255 (fractional part).
        hi = [87, 87, 88, 88, 87, 88]
        lo = [10, 200, 40, 250, 5, 230]
        words = triage.detect_words([("hi", hi), ("lo", lo)])
        assert words
        assert (words[0].hi_key, words[0].lo_key) == ("hi", "lo")
        assert words[0].score > 0.3

    def test_no_word_when_both_wide(self):
        a = [10, 200, 40, 250, 5]
        b = [230, 5, 190, 20, 240]
        assert triage.detect_words([("a", a), ("b", b)]) == []

    def test_no_word_when_both_constant(self):
        assert triage.detect_words([("a", [5, 5, 5]), ("b", [9, 9, 9])]) == []

    def test_ranks_by_score(self):
        cols = [
            ("h1", [87, 87, 88, 88]),  # narrow hi
            ("l1", [0, 255, 10, 240]),  # very wide lo -> strong
            ("h2", [30, 10, 25, 5]),  # wider hi
            ("l2", [100, 180, 120, 160]),  # narrower lo -> weaker/none
        ]
        words = triage.detect_words(cols)
        assert words
        assert words[0].hi_key == "h1"  # strongest first
