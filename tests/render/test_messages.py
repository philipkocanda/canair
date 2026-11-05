"""The four message levels: one form, on stderr, coloured by the stream they go to."""

from __future__ import annotations

import io

import pytest

from canair.render import messages


@pytest.fixture
def colour(monkeypatch):
    monkeypatch.setenv("FORCE_COLOR", "1")
    monkeypatch.delenv("NO_COLOR", raising=False)


@pytest.fixture
def plain(monkeypatch):
    monkeypatch.delenv("FORCE_COLOR", raising=False)
    monkeypatch.setenv("NO_COLOR", "1")


@pytest.mark.parametrize(
    ("func", "level"),
    [
        (messages.error, "error"),
        (messages.warning, "warning"),
        (messages.note, "note"),
        (messages.hint, "hint"),
    ],
)
def test_level_prefix_goes_to_stderr_only(func, level, plain, capsys):
    func("it broke")
    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err == f"{level}: it broke\n"


def test_a_continuation_line_is_indented(plain, capsys):
    messages.error("first\nsecond")
    assert capsys.readouterr().err == "error: first\n  second\n"


def test_colours_follow_the_level(colour, capsys):
    messages.error("a")
    messages.warning("b")
    messages.note("c")
    messages.hint("d")
    err = capsys.readouterr().err.splitlines()
    assert err[0] == "\x1b[91merror: a\x1b[0m"
    assert err[1] == "\x1b[93mwarning: b\x1b[0m"
    assert err[2] == "note: c"
    assert err[3] == "\x1b[2mhint:\x1b[0m d"


def test_colour_is_gated_by_the_stream_written_to(monkeypatch):
    monkeypatch.delenv("FORCE_COLOR", raising=False)
    monkeypatch.delenv("NO_COLOR", raising=False)
    buf = io.StringIO()  # not a terminal
    messages.error("x", file=buf)
    assert buf.getvalue() == "error: x\n"


def test_fail_prints_and_exits_with_the_code(plain, capsys):
    with pytest.raises(SystemExit) as exc:
        messages.fail("no such ECU", code=2)
    assert exc.value.code == 2
    assert capsys.readouterr().err == "error: no such ECU\n"
