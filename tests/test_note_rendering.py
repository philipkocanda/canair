"""Tests for the shared free-text (notes) rendering policy (canlib.yaml_rt).

Both writer subsystems — the text-based per-field editor (canlib.pids_edit._text)
and the ruamel round-trip writer (canlib.ecus_edit via canlib.yaml_rt.folded) —
render notes the same way: inline when short, else a wrapped folded ``>-`` block
that round-trips to the original text. These tests pin that policy at the seam.
"""

from __future__ import annotations

import io

import pytest
import yaml
from ruamel.yaml.comments import CommentedMap
from ruamel.yaml.scalarstring import FoldedScalarString

from canlib.pids_edit._text import _format_block_scalar
from canlib.yaml_rt import (
    NOTE_WRAP_WIDTH,
    dump,
    folded,
    note_foldable,
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
        assert lines == ["para one", "", "", "para two"]  # one blank line per newline

    def test_preserves_single_line_breaks(self):
        assert wrap_note_lines("line one\nline two", width=40) == ["line one", "", "line two"]

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

    def test_line_breaks_round_trip(self):
        # Each newline in the value is one blank line in the folded block, so a
        # single break and a paragraph break both survive a parse.
        for sep in ("\n", "\n\n"):
            val = LONG + sep + "Source: https://example.com/profile.json"
            doc = yaml.safe_load("\n".join(_format_block_scalar("    ", "notes", val)))
            assert doc["notes"] == val

    def test_matches_the_ruamel_rendering(self):
        val = LONG + "\n\nSource: https://example.com/profile.json"
        m = CommentedMap()
        m["notes"] = folded(val, key_indent=0)
        buf = io.StringIO()
        dump(m, buf)
        assert _format_block_scalar("", "notes", val) == buf.getvalue().splitlines()


def _read_both(doc: str):
    """``doc`` loaded by PyYAML and by the round-trip reader (warnings are failures)."""
    import warnings

    with warnings.catch_warnings():
        warnings.simplefilter("error")
        return yaml.safe_load(doc), round_trip_yaml().load(doc)


def _text_path_reads_back(value, indent="    "):
    lines = _format_block_scalar(indent, "notes", value)
    doc = ("k:\n" if indent else "") + "\n".join(lines) + "\n"
    py, rt = _read_both(doc)
    if indent:
        py, rt = py["k"], rt["k"]
    assert py["notes"] == value
    assert str(rt["notes"]) == value
    assert all(len(ln) <= NOTE_WRAP_WIDTH for ln in lines), lines
    return lines


def _ruamel_path_reads_back(value):
    m = CommentedMap()
    m["notes"] = folded(value, key_indent=0)
    buf = io.StringIO()
    dump(m, buf)
    py, rt = _read_both(buf.getvalue())
    assert py["notes"] == value
    assert str(rt["notes"]) == value
    return buf.getvalue().splitlines()


BULLETS = "Intro line that explains the list:\n  - item a\n  - item b"


class TestMoreIndentedLines:
    """A break next to a more-indented line is literal in a folded scalar."""

    def test_bullets_get_no_spurious_blank_lines(self):
        assert wrap_note_lines(BULLETS, width=80) == [
            "Intro line that explains the list:",
            "  - item a",
            "  - item b",
        ]

    @pytest.mark.parametrize(
        "value",
        [
            BULLETS,
            BULLETS + "\nback to a normal line",
            BULLETS + "\n\nnormal paragraph after a blank line",
            "normal\n\n\n  indented after two blank lines\nnormal again",
            "start\n    deeper continuation\n  less deep\nend",
            LONG + "\n  - " + LONG,  # a long more-indented line is never wrapped
        ],
    )
    def test_round_trip_and_parity_with_ruamel(self, value):
        lines = _format_block_scalar("", "notes", value)
        assert _ruamel_path_reads_back(value) == lines
        doc = "\n".join(lines) + "\n"
        py, rt = _read_both(doc)
        assert py["notes"] == value and str(rt["notes"]) == value


class TestValuesFoldingCannotRepresent:
    """Fall back to an exact double-quoted scalar instead of losing content."""

    @pytest.mark.parametrize(
        "value",
        [
            "trailing space on the first line \nsecond line",
            "\nleading newline then text",
            "text then a trailing newline\n",
            "  first line starts indented\nsecond line",
            "multi-line with a line separator\u2028inside\nand another line",
            "tab-led line follows\n\tlike this",
            "a whitespace-only line\n   \nfollows",
            "control \x07 char\nsecond line",
            LONG + "  " + LONG + " \n" + LONG,
        ],
    )
    def test_text_path_is_exact_and_wrapped(self, value):
        assert not note_foldable(value)
        _text_path_reads_back(value)
        _text_path_reads_back(value, indent="")

    @pytest.mark.parametrize(
        "value",
        [
            "  first line starts indented\nsecond line",
            "\nleading newline",
            "u2028\u2028inline",
            "trailing spaces   ",
        ],
    )
    def test_ruamel_path_is_exact(self, value):
        _ruamel_path_reads_back(value)

    @pytest.mark.parametrize("value", ["  padded both sides  ", "inline\u2028separator", "\x07"])
    def test_inline_values_keep_their_content(self, value):
        lines = _text_path_reads_back(value)
        assert len(lines) == 1

    def test_double_space_at_a_wrap_point_survives(self):
        # Every sentence ends in a double space; wrapping there would collapse it.
        value = "  ".join(["This sentence is about forty characters."] * 6)
        assert note_foldable(value)
        lines = _text_path_reads_back(value)
        assert lines[0].endswith(">-")
        assert _ruamel_path_reads_back(value) == _format_block_scalar("", "notes", value)

    def test_double_quoted_continuation_never_starts_with_a_bare_space(self):
        value = ("x" * 50 + "   ") * 6 + "\n"  # runs of spaces at every chunk edge
        lines = _text_path_reads_back(value)
        assert all(not ln[len("      ") :].startswith(" ") for ln in lines[1:])


class TestDocstringsMatchBehaviour:
    def test_note_foldable_accepts_ordinary_multiline_text(self):
        assert note_foldable(LONG + "\n\n" + BULLETS)
        assert not note_foldable("")
