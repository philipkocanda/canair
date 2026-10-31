"""Tests for the shared YAML loading helper (canlib.yaml_io)."""

import io

import yaml

from canlib import yaml_io


def test_prefers_c_loader_when_available():
    """The shared loader uses libyaml's CSafeLoader when the build has it.

    The whole point of the module is the faster C parser; if libyaml is present
    we must actually be pointed at it (not the pure-Python fallback).
    """
    if hasattr(yaml, "CSafeLoader"):
        assert yaml_io.SafeLoader is yaml.CSafeLoader
    else:  # pragma: no cover - depends on the libyaml build
        assert yaml_io.SafeLoader is yaml.SafeLoader


def test_safe_load_parses_string_like_pyyaml():
    doc = "a: 1\nb: [x, y]\nc: {d: true}\n"
    assert yaml_io.safe_load(doc) == yaml.safe_load(doc)


def test_safe_load_accepts_file_object():
    doc = "sessions:\n  - date: '2026-01-01'\n    captures: []\n"
    assert yaml_io.safe_load(io.StringIO(doc)) == yaml.safe_load(doc)


def test_load_path_reads_file(tmp_path):
    p = tmp_path / "sample.yaml"
    p.write_text("ecu: BMS\npid: '2101'\n")
    assert yaml_io.load_path(p) == {"ecu": "BMS", "pid": "2101"}


def test_empty_document_is_none():
    assert yaml_io.safe_load("") is None


class TestDuplicateKeys:
    def test_a_clean_document_has_none(self):
        assert yaml_io.duplicate_keys("a: 1\nb: {c: 2, d: [x, {e: 3}]}\n") == []

    def test_a_repeated_key_is_reported_with_its_path_and_lines(self):
        doc = "VCU:\n  pids:\n    21F0:\n      parameters:\n        GEAR: 1\n        GEAR: 2\n"
        (dup,) = yaml_io.duplicate_keys(doc)
        assert dup == yaml_io.DuplicateKey(("VCU", "pids", "21F0", "parameters"), "GEAR", 6, 5)

    def test_quoted_and_bare_keys_are_the_same_key(self):
        (dup,) = yaml_io.duplicate_keys("pids:\n  2101: {}\n  '2101': {}\n")
        assert dup.key == "2101"

    def test_flow_mappings_and_sequences_are_searched(self):
        dups = yaml_io.duplicate_keys("top: {a: 1, a: 2}\nlist:\n  - {b: 1}\n  - {b: 1, b: 2}\n")
        assert [(d.path, d.key) for d in dups] == [(("top",), "a"), (("list", "[1]"), "b")]

    def test_the_same_key_in_sibling_mappings_is_fine(self):
        assert yaml_io.duplicate_keys("x: {unit: V}\ny: {unit: V}\n") == []

    def test_an_alias_is_not_reported_twice(self):
        doc = "base: &b {k: 1, k: 2}\nuse: *b\nmerged:\n  <<: *b\n  j: 3\n"
        assert [d.path for d in yaml_io.duplicate_keys(doc)] == [("base",)]

    def test_what_the_loader_does_with_one(self):
        # Why the check exists: the loader keeps the later value without a word.
        assert yaml_io.safe_load("k: 1\nk: 2\n") == {"k": 2}
