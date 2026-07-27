"""Tests for canlib.keepmode helpers (T2)."""

from canlib.keepmode import keep_mode_from_args, scope_is_keep_unique


class _Args:
    def __init__(self, **kw):
        self.keep_all = False
        self.keep = None
        self.__dict__.update(kw)


class TestKeepModeFromArgs:
    def test_default_is_unique(self):
        # No keep flag => unique-dedup default (small capture files).
        assert keep_mode_from_args(_Args()) == "unique"

    def test_keep_all_overrides(self):
        assert keep_mode_from_args(_Args(keep_all=True)) == "all"

    def test_keep_n_is_last(self):
        assert keep_mode_from_args(_Args(keep=5)) == "last"

    def test_keep_all_beats_keep_n(self):
        assert keep_mode_from_args(_Args(keep_all=True, keep=5)) == "all"

    def test_tolerates_missing_attrs(self):
        assert keep_mode_from_args(object()) == "unique"


class TestScopeIsKeepUnique:
    def test_detects_unique(self):
        assert scope_is_keep_unique([{"keep_mode": "unique"}]) is True

    def test_mixed_scope(self):
        caps = [{"keep_mode": ""}, {"keep_mode": "unique"}, {}]
        assert scope_is_keep_unique(caps) is True

    def test_none_when_absent(self):
        assert scope_is_keep_unique([{"keep_mode": ""}, {}]) is False

    def test_all_and_last_are_not_flagged(self):
        assert scope_is_keep_unique([{"keep_mode": "all"}, {"keep_mode": "last"}]) is False

    def test_tolerates_non_dict_entries(self):
        # Fake capture lists in some tests carry non-dicts; must not raise.
        assert scope_is_keep_unique([1, 2, 3]) is False
