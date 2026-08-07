"""Unit tests for :mod:`canlib.ansi` — the palette + colour-gating policy.

These pin the policy itself. Command-level gating (that piped commands emit
zero escapes across the full CLI surface) is proven by
``tests/test_ansi_gating.py``.
"""

from __future__ import annotations

import io

import pytest

from canlib import ansi


@pytest.fixture(autouse=True)
def _clean_color_env(monkeypatch):
    """Every test starts with ``NO_COLOR``/``FORCE_COLOR`` unset."""
    monkeypatch.delenv("NO_COLOR", raising=False)
    monkeypatch.delenv("FORCE_COLOR", raising=False)


class _FakeStream:
    def __init__(self, *, isatty: bool) -> None:
        self._isatty = isatty

    def isatty(self) -> bool:
        return self._isatty


class TestRawPalette:
    def test_raw_codes_match_the_codes_the_tree_actually_used(self) -> None:
        # If any of these change, every module that hand-declared a copy of the
        # constant would silently disagree with the new central palette.
        assert ansi.raw.BOLD == "\033[1m"
        assert ansi.raw.DIM == "\033[2m"
        assert ansi.raw.RED == "\033[91m"
        assert ansi.raw.GREEN == "\033[92m"
        assert ansi.raw.YELLOW == "\033[93m"
        assert ansi.raw.CYAN == "\033[96m"
        assert ansi.raw.RESET == "\033[0m"


class TestGatedPalette:
    def test_gated_names_return_raw_codes_on_a_tty(self, monkeypatch) -> None:
        # Force stdout to look like a TTY so the gate opens.
        monkeypatch.setattr("sys.stdout", _FakeStream(isatty=True))
        assert ansi.BOLD == ansi.raw.BOLD
        assert ansi.RESET == ansi.raw.RESET
        assert ansi.CYAN == ansi.raw.CYAN

    def test_gated_names_return_empty_when_piped(self, monkeypatch) -> None:
        monkeypatch.setattr("sys.stdout", _FakeStream(isatty=False))
        assert ansi.BOLD == ""
        assert ansi.RESET == ""
        assert ansi.CYAN == ""

    def test_gated_names_return_empty_with_no_color(self, monkeypatch) -> None:
        monkeypatch.setattr("sys.stdout", _FakeStream(isatty=True))
        monkeypatch.setenv("NO_COLOR", "1")
        assert ansi.BOLD == ""

    def test_gated_names_return_codes_with_force_color(self, monkeypatch) -> None:
        monkeypatch.setattr("sys.stdout", _FakeStream(isatty=False))
        monkeypatch.setenv("FORCE_COLOR", "1")
        assert ansi.BOLD == ansi.raw.BOLD

    def test_unknown_attribute_still_raises(self) -> None:
        # __getattr__ falls through — a typo should still error, not silently
        # produce an empty string.
        with pytest.raises(AttributeError, match="MAGENTA"):
            _ = ansi.MAGENTA


class TestUseColor:
    def test_tty_stream_enables_colour(self) -> None:
        assert ansi.use_color(_FakeStream(isatty=True)) is True

    def test_non_tty_stream_disables_colour(self) -> None:
        assert ansi.use_color(_FakeStream(isatty=False)) is False

    def test_no_color_wins_over_a_tty(self, monkeypatch) -> None:
        monkeypatch.setenv("NO_COLOR", "1")
        assert ansi.use_color(_FakeStream(isatty=True)) is False

    def test_no_color_any_nonempty_value_disables(self, monkeypatch) -> None:
        # The NO_COLOR convention: any non-empty value counts.
        monkeypatch.setenv("NO_COLOR", "yes")
        assert ansi.use_color(_FakeStream(isatty=True)) is False

    def test_no_color_empty_does_not_disable(self, monkeypatch) -> None:
        monkeypatch.setenv("NO_COLOR", "")
        assert ansi.use_color(_FakeStream(isatty=True)) is True

    def test_force_color_enables_on_a_pipe(self, monkeypatch) -> None:
        monkeypatch.setenv("FORCE_COLOR", "1")
        assert ansi.use_color(_FakeStream(isatty=False)) is True

    def test_force_color_beats_no_color(self, monkeypatch) -> None:
        # The convention when both are set: FORCE_COLOR wins. This matches how
        # the screenshot harness sets FORCE_COLOR=1 to render into files.
        monkeypatch.setenv("FORCE_COLOR", "1")
        monkeypatch.setenv("NO_COLOR", "1")
        assert ansi.use_color(_FakeStream(isatty=False)) is True

    def test_default_stream_is_stdout(self, monkeypatch) -> None:
        # A plain io.StringIO has no isatty() returning True, so colour is off
        # by default when we swap stdout for one.
        fake = io.StringIO()
        monkeypatch.setattr("sys.stdout", fake)
        assert ansi.use_color() is False

    def test_stream_without_isatty_disables(self) -> None:
        # Some file-like objects don't implement isatty() at all.
        class Bare:
            pass

        assert ansi.use_color(Bare()) is False


class TestC:
    def test_wraps_when_colour_on(self) -> None:
        out = ansi.c("hi", ansi.raw.YELLOW, stream=_FakeStream(isatty=True))
        assert out == f"{ansi.raw.YELLOW}hi{ansi.raw.RESET}"

    def test_returns_plain_when_colour_off(self) -> None:
        assert ansi.c("hi", ansi.raw.YELLOW, stream=_FakeStream(isatty=False)) == "hi"

    def test_no_color_disables_wrapping(self, monkeypatch) -> None:
        monkeypatch.setenv("NO_COLOR", "1")
        assert ansi.c("hi", ansi.raw.YELLOW, stream=_FakeStream(isatty=True)) == "hi"


class TestCerr:
    def test_gated_on_stderr_not_stdout(self, monkeypatch) -> None:
        # stdout is a TTY, stderr is a pipe → cerr must NOT colour.
        monkeypatch.setattr("sys.stdout", _FakeStream(isatty=True))
        monkeypatch.setattr("sys.stderr", _FakeStream(isatty=False))
        assert ansi.cerr("warn", ansi.raw.RED) == "warn"

    def test_colours_when_stderr_is_a_tty(self, monkeypatch) -> None:
        monkeypatch.setattr("sys.stdout", _FakeStream(isatty=False))
        monkeypatch.setattr("sys.stderr", _FakeStream(isatty=True))
        assert ansi.cerr("warn", ansi.raw.RED) == f"{ansi.raw.RED}warn{ansi.raw.RESET}"
