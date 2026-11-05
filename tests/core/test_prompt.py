"""The shared confirmation helper: one set of rules for every prompt."""

from __future__ import annotations

import io

import pytest

from canair.core.prompt import ABORTED, PromptUnavailable, confirm, typed_confirm


class _Stdin(io.StringIO):
    def __init__(self, text: str, tty: bool):
        super().__init__(text)
        self._tty = tty

    def isatty(self) -> bool:
        return self._tty


@pytest.fixture
def stdin(monkeypatch):
    def _set(text: str = "", *, tty: bool = True) -> None:
        monkeypatch.setattr("sys.stdin", _Stdin(text, tty))

    return _set


class TestConfirm:
    def test_yes_flag_answers_without_reading(self, stdin, capsys):
        stdin(tty=False)
        assert confirm("Go?", yes=True) is True
        assert capsys.readouterr().err == ""

    @pytest.mark.parametrize("answer", ["y\n", "Y\n", "yes\n", " YES \n"])
    def test_explicit_yes_confirms(self, stdin, answer):
        stdin(answer)
        assert confirm("Go?") is True

    @pytest.mark.parametrize("answer", ["\n", "n\n", "no\n", "maybe\n", ""])
    def test_anything_else_declines_and_says_so(self, stdin, capsys, answer):
        stdin(answer)
        assert confirm("Go?") is False
        err = capsys.readouterr().err
        assert ABORTED in err

    def test_question_and_default_go_to_stderr(self, stdin, capsys):
        stdin("y\n")
        confirm("Clear the codes?")
        captured = capsys.readouterr()
        assert captured.out == ""
        assert "Clear the codes? [y/N]" in captured.err

    def test_danger_prompt_has_the_double_bang_lead(self, stdin, capsys):
        stdin("n\n")
        confirm("Actuate?", danger=True)
        assert "!! Actuate? [y/N]" in capsys.readouterr().err

    def test_no_terminal_refuses_and_names_the_flag(self, stdin):
        stdin("y\n", tty=False)
        with pytest.raises(PromptUnavailable) as exc:
            confirm("Send SIGTERM to PID 7?")
        assert "--yes" in str(exc.value)
        assert "stdin is not a terminal" in str(exc.value)

    def test_a_different_flag_is_named(self, stdin):
        stdin(tty=False)
        with pytest.raises(PromptUnavailable, match="--force"):
            confirm("Go?", flag="--force")


class TestTypedConfirm:
    def test_the_exact_word_confirms(self, stdin):
        stdin("YES\n")
        assert typed_confirm("Bypass the blocklist.", "YES") is True

    @pytest.mark.parametrize("answer", ["yes\n", "y\n", "\n", ""])
    def test_anything_else_declines(self, stdin, capsys, answer):
        stdin(answer)
        assert typed_confirm("Bypass the blocklist.", "YES") is False
        assert ABORTED in capsys.readouterr().err

    def test_no_terminal_refuses(self, stdin):
        stdin("YES\n", tty=False)
        with pytest.raises(PromptUnavailable):
            typed_confirm("Bypass the blocklist.", "YES")

    def test_yes_flag_skips_the_word(self, stdin):
        stdin(tty=False)
        assert typed_confirm("Bypass.", "YES", yes=True) is True
