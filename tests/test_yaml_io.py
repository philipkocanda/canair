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
