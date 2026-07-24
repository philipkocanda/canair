"""Tests for canlib.keepmode helpers (T2)."""

from canlib.keepmode import scope_is_keep_unique


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
