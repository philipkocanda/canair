"""Tests for the shared free-text (notes) rendering policy (canlib.yaml_rt).

Both writer subsystems — the text-based per-field editor (canlib.pids_edit._text)
and the ruamel round-trip writer (canlib.ecus_edit via canlib.yaml_rt.folded) —
render notes the same way: inline when short, else a wrapped folded ``>-`` block
that round-trips to the original text. These tests pin that policy at the seam.
"""

from __future__ import annotations

import io

import yaml
from ruamel.yaml.comments import CommentedMap
from ruamel.yaml.scalarstring import FoldedScalarString

from canlib.pids_edit._text import _format_block_scalar
from canlib.yaml_rt import (
    NOTE_WRAP_WIDTH,
    folded,
    note_should_inline,
    round_trip_yaml,
    wrap_note_lines,
)

LONG = (
    "Seeded device-free from the upstream WiCAN community profile meatpiHQ/wican-fw "
    "vehicle_profiles/xpeng/xpeng_g6.json. The upstream routes every DID through a "
    "single responder at request 0x704 / response 0x784. Named BMS; unverified."
)


class TestNoteShouldInline:
    def test_short_is_inline(self):
        assert note_should_inline("short note", prefix_len=11)

    def test_long_is_not_inline(self):
        assert not note_should_inline(LONG, prefix_len=11)

    def test_multiline_is_not_inline(self):
        assert not note_should_inline("line one\nline two", prefix_len=4)

    def test_boundary(self):
        val = "x" * 88
        assert note_should_inline(val, prefix_len=12)  # 100 exactly
        assert not note_should_inline(val, prefix_len=13)  # 101


class TestWrapNoteLines:
    def test_wraps_within_width(self):
        lines = wrap_note_lines(LONG, width=90)
        assert all(len(ln) <= 90 for ln in lines)
        # Folds back to the original single-line text (spaces between lines).
        assert " ".join(ln for ln in lines if ln) == LONG

    def test_preserves_paragraph_breaks(self):
        lines = wrap_note_lines("para one\n\npara two", width=40)
        assert "" in lines  # blank line kept as a paragraph break

    def test_long_token_not_broken(self):
        url = "https://example.com/" + "a" * 120
        assert wrap_note_lines(url, width=40) == [url]  # unbreakable token intact


class TestFolded:
    def _dump(self, value):
        m = CommentedMap()
        m["notes"] = value
        buf = io.StringIO()
        round_trip_yaml().dump(m, buf)
        return buf.getvalue()

    def test_short_returns_plain_str(self):
        out = folded("short note", key_indent=4)
        assert out == "short note"
        assert not isinstance(out, FoldedScalarString)

    def test_long_returns_wrapped_folded_scalar(self):
        out = folded(LONG, key_indent=4)
        assert isinstance(out, FoldedScalarString)
        text = self._dump(out)
        assert "notes: >-" in text
        assert max(len(ln) for ln in text.splitlines()) <= NOTE_WRAP_WIDTH
        assert yaml.safe_load(text)["notes"] == LONG

    def test_multi_paragraph_wraps_and_preserves_breaks(self):
        val = (
            LONG
            + "\nSecond paragraph that is also fairly long and should wrap onto its own lines here."
        )
        out = folded(val, key_indent=4)
        text = self._dump(out)
        assert "notes: >-" in text
        assert max(len(ln) for ln in text.splitlines()) <= NOTE_WRAP_WIDTH
        assert yaml.safe_load(text)["notes"] == val  # paragraph break + wrap preserved


class TestFormatBlockScalarTextPath:
    def test_short_is_inline(self):
        assert _format_block_scalar("    ", "notes", "short note") == ["    notes: short note"]

    def test_long_folds_and_wraps(self):
        lines = _format_block_scalar("    ", "notes", LONG)
        assert lines[0] == "    notes: >-"
        assert all(len(ln) <= NOTE_WRAP_WIDTH for ln in lines)
        # Round-trips through a YAML parse to the original text.
        doc = yaml.safe_load("\n".join(lines))
        assert doc["notes"] == LONG

    def test_value_with_colon_quoted_inline(self):
        # A short note with ": " must be quoted when rendered inline.
        lines = _format_block_scalar("    ", "notes", "see this: a note")
        assert yaml.safe_load("\n".join(lines))["notes"] == "see this: a note"
