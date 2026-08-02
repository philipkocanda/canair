"""Tests for canlib.keepmode helpers (T2)."""

from canlib.keepmode import (
    keep_mode_from_args,
    scope_is_keep_changes,
    scope_is_keep_unique,
)


class _Args:
    def __init__(self, **kw):
        self.keep_changes = False
        self.keep_unique = False
        self.keep_all = False
        self.keep = None
        self.__dict__.update(kw)


class TestKeepModeFromArgs:
    def test_default_is_changes(self):
        # No keep flag => run-length "changes" default (preserves transitions,
        # still compact for stationary signals).
        assert keep_mode_from_args(_Args()) == "changes"

    def test_keep_changes_explicit(self):
        assert keep_mode_from_args(_Args(keep_changes=True)) == "changes"

    def test_keep_unique_is_legacy_global(self):
        assert keep_mode_from_args(_Args(keep_unique=True)) == "unique"

    def test_keep_all_overrides(self):
        assert keep_mode_from_args(_Args(keep_all=True)) == "all"

    def test_keep_n_is_last(self):
        assert keep_mode_from_args(_Args(keep=5)) == "last"

    def test_keep_all_beats_keep_n(self):
        assert keep_mode_from_args(_Args(keep_all=True, keep=5)) == "all"

    def test_keep_all_beats_keep_unique(self):
        assert keep_mode_from_args(_Args(keep_all=True, keep_unique=True)) == "all"

    def test_tolerates_missing_attrs(self):
        assert keep_mode_from_args(object()) == "changes"


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

    def test_changes_is_not_unique(self):
        assert scope_is_keep_unique([{"keep_mode": "changes"}]) is False

    def test_tolerates_non_dict_entries(self):
        # Fake capture lists in some tests carry non-dicts; must not raise.
        assert scope_is_keep_unique([1, 2, 3]) is False


class TestScopeIsKeepChanges:
    def test_detects_changes(self):
        assert scope_is_keep_changes([{"keep_mode": "changes"}]) is True

    def test_mixed_scope(self):
        assert scope_is_keep_changes([{"keep_mode": "unique"}, {"keep_mode": "changes"}]) is True

    def test_unique_is_not_changes(self):
        assert scope_is_keep_changes([{"keep_mode": "unique"}]) is False

    def test_none_when_absent(self):
        assert scope_is_keep_changes([{"keep_mode": ""}, {}]) is False

    def test_tolerates_non_dict_entries(self):
        assert scope_is_keep_changes([1, 2, 3]) is False
