"""The session label prompt: a question nobody can answer never costs the recording."""

from __future__ import annotations

import io

import pytest

from canair.captures.metadata import prompt_metadata, resolve_metadata


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


def _answers(monkeypatch, *answers: str) -> None:
    it = iter(answers)
    monkeypatch.setattr("builtins.input", lambda _prompt="": next(it))


class TestLabelFlag:
    def test_a_label_flag_never_prompts(self, stdin, monkeypatch):
        stdin()  # even on a terminal
        monkeypatch.setattr("builtins.input", lambda _p="": pytest.fail("should not prompt"))
        meta = resolve_metadata("drive", "ready, parked", "n", suggested_label="x")
        assert meta == ("drive", ["READY", "PARKED"], "n")

    def test_a_label_flag_defaults_empty_states_and_notes(self, stdin):
        stdin(tty=False)
        assert resolve_metadata("Only label", None, None) == ("Only label", [], "")


class TestNoTerminal:
    def test_saves_under_the_suggested_label_and_says_how_to_remove_it(self, stdin, capsys):
        stdin("ignored\n", tty=False)
        meta = resolve_metadata(None, None, None, suggested_label="Multi query session")
        assert meta is not None
        assert meta[0] == "Multi query session"
        err = capsys.readouterr().err
        assert "no terminal" in err
        assert "--label" in err
        assert "captures uds --delete --last-session" in err

    def test_state_and_notes_flags_are_kept(self, stdin):
        stdin(tty=False)
        meta = resolve_metadata(None, "READY", "a note", suggested_label="s")
        assert meta == ("s", ["READY"], "a note")

    def test_the_suggested_state_fills_in_when_no_flag_names_one(self, stdin):
        stdin(tty=False)
        meta = resolve_metadata(None, None, None, suggested_label="s", last_state="CHARGING")
        assert meta is not None and meta[1] == ["CHARGING"]

    def test_without_a_suggestion_the_label_is_generated_not_dropped(self, stdin):
        stdin(tty=False)
        meta = resolve_metadata(None, None, None)
        assert meta is not None
        assert meta[0].startswith("Unlabelled session ")

    def test_prompt_metadata_does_not_wait_on_a_pipe(self, stdin, monkeypatch):
        stdin(tty=False)

        def _no_input(_prompt=""):
            raise AssertionError("must not read a pipe")

        monkeypatch.setattr("builtins.input", _no_input)
        assert prompt_metadata(suggested_label="s") is not None


class TestInteractive:
    def test_no_label_falls_back_to_the_prompt(self, stdin, monkeypatch):
        stdin()
        _answers(monkeypatch, "Prompted", "charging", "n")
        meta = resolve_metadata(None, None, None, suggested_label="sugg")
        assert meta == ("Prompted", ["CHARGING"], "n")

    def test_enter_accepts_the_suggestion(self, stdin, monkeypatch):
        stdin()
        _answers(monkeypatch, "", "", "")
        assert prompt_metadata(suggested_label="s") == ("s", [], "")

    def test_typed_answers_are_used(self, stdin, monkeypatch):
        stdin()
        _answers(monkeypatch, "my label", "acc, run", "hello")
        assert prompt_metadata(suggested_label="s") == ("my label", ["ACC", "RUN"], "hello")

    def test_end_of_input_saves_instead_of_discarding(self, stdin, monkeypatch, capsys):
        stdin()

        def _eof(_prompt=""):
            raise EOFError

        monkeypatch.setattr("builtins.input", _eof)
        meta = prompt_metadata(suggested_label="s")
        assert meta is not None and meta[0] == "s"
        assert "ended early" in capsys.readouterr().err

    def test_ctrl_c_saves_instead_of_discarding(self, stdin, monkeypatch):
        stdin()

        def _interrupt(_prompt=""):
            raise KeyboardInterrupt

        monkeypatch.setattr("builtins.input", _interrupt)
        meta = prompt_metadata(suggested_label="s")
        assert meta is not None and meta[0] == "s"

    def test_an_empty_label_with_no_suggestion_is_a_deliberate_skip(self, stdin, monkeypatch):
        stdin()
        _answers(monkeypatch, "")
        assert prompt_metadata() is None
