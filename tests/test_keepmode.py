"""Tests for canlib.keepmode helpers (T2)."""

import pytest

from canlib.keepmode import (
    KEEP_ALL,
    KEEP_CHANGES,
    KEEP_LAST,
    KEEP_MODES,
    KEEP_UNIQUE,
    PERSISTED_KEEP_MODES,
    keep_mode_from_args,
    parse_keep_mode,
    persisted_keep_mode,
    scope_is_keep_changes,
    scope_is_keep_unique,
    wants_save,
)


class _Args:
    def __init__(self, **kw):
        self.keep_changes = False
        self.keep_all = False
        self.keep = None
        self.__dict__.update(kw)


class TestKeepModeFromArgs:
    def test_default_is_all(self):
        # No keep flag => every poll is recorded (the on-disk folding keeps the
        # capture file compact for stationary signals).
        assert keep_mode_from_args(_Args()) == "all"

    def test_keep_all_explicit(self):
        assert keep_mode_from_args(_Args(keep_all=True)) == "all"

    def test_keep_changes_overrides(self):
        assert keep_mode_from_args(_Args(keep_changes=True)) == "changes"

    def test_keep_n_is_last(self):
        assert keep_mode_from_args(_Args(keep=5)) == "last"

    def test_tolerates_missing_attrs(self):
        assert keep_mode_from_args(object()) == "all"

    def test_keep_unique_flag_is_gone(self):
        from canlib.cli import build_parser

        parser = build_parser()
        assert parser.parse_args(["monitor", "BMS"]).keep_all is False
        with pytest.raises(SystemExit):
            parser.parse_args(["monitor", "BMS", "--keep-unique"])


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


class TestKeepModeVocabulary:
    """The two vocabularies are derived from their `Literal`s and stay distinct."""

    def test_keep_modes_derive_from_the_literal(self):
        from typing import get_args

        from canlib.keepmode import KeepMode

        assert KEEP_MODES == get_args(KeepMode)

    def test_persisted_modes_derive_from_the_literal(self):
        from typing import get_args

        from canlib.keepmode import PersistedKeepMode

        assert PERSISTED_KEEP_MODES == get_args(PersistedKeepMode)

    def test_only_the_dedup_policy_is_both_recorded_and_persisted(self):
        # `all`/`last` are legitimate recording policies but must never be
        # persisted — they applied no dedup, so absence is the honest record.
        # `unique` is the reverse: provenance older sessions carry, never recorded.
        assert set(PERSISTED_KEEP_MODES) & set(KEEP_MODES) == {KEEP_CHANGES}
        assert set(KEEP_MODES) - set(PERSISTED_KEEP_MODES) == {KEEP_ALL, KEEP_LAST}
        assert set(PERSISTED_KEEP_MODES) - set(KEEP_MODES) == {KEEP_UNIQUE}

    def test_every_mode_is_reachable_from_args(self):
        # No mode may be declared-but-unproducible (or vice versa).
        produced = {
            keep_mode_from_args(_Args()),
            keep_mode_from_args(_Args(keep_changes=True)),
            keep_mode_from_args(_Args(keep=5)),
        }
        assert produced == set(KEEP_MODES)


class TestPersistedKeepMode:
    def test_dedup_modes_are_persisted(self):
        assert persisted_keep_mode("changes") == KEEP_CHANGES
        assert persisted_keep_mode("unique") == KEEP_UNIQUE

    def test_non_dedup_modes_are_not_persisted(self):
        # The rule this helper centralises: "no dedup applied" → omit the field.
        assert persisted_keep_mode("all") is None
        assert persisted_keep_mode("last") is None

    def test_none_and_garbage_are_not_persisted(self):
        assert persisted_keep_mode(None) is None
        assert persisted_keep_mode("") is None
        assert persisted_keep_mode("Unique") is None  # case-sensitive on purpose


class TestParseKeepMode:
    def test_accepts_every_known_mode(self):
        for mode in KEEP_MODES:
            assert parse_keep_mode(mode) == mode

    def test_the_retired_unique_mode_is_not_a_recording_policy(self):
        assert parse_keep_mode("unique") is None

    def test_rejects_unknown_and_non_string(self):
        assert parse_keep_mode("uniqe") is None
        assert parse_keep_mode(None) is None
        assert parse_keep_mode(True) is None
        assert parse_keep_mode({"keep_mode": "unique"}) is None


class TestWantsSave:
    """``wants_save``: does this invocation intend to record captures?

    The metadata flags count as intent on their own — ``--label`` without
    ``--save`` has nowhere to put the label, so treating it as a no-op would
    silently discard it. The predicate also drives whether a dropped session
    points the user at ``--recover``, which is why ``modes.raw_monitor`` needs it
    and why it lives here rather than in the CLI layer.
    """

    def test_no_flags_is_false(self):
        assert wants_save(_Args()) is False

    def test_save_flag(self):
        assert wants_save(_Args(save=True)) is True

    def test_metadata_alone_counts_as_intent(self):
        assert wants_save(_Args(label="charging")) is True
        assert wants_save(_Args(state="READY")) is True
        assert wants_save(_Args(notes="hood open")) is True

    def test_empty_string_metadata_still_counts(self):
        """`--label ""` was passed explicitly; None means "not passed"."""
        assert wants_save(_Args(label="")) is True

    def test_tolerates_a_namespace_missing_the_attributes(self):
        """Callers include modes that build a partial namespace."""

        class Bare:
            pass

        assert wants_save(Bare()) is False
