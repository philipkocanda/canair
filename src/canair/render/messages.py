"""The four message levels every command prints, in one form.

``error: …``, ``warning: …``, ``note: …`` and ``hint: …``: lower case, not indented, on stderr,
coloured when *stderr* is a terminal (red, yellow, plain, dim prefix). A command raises a typed
error (``ProfileError``, ``EditError``, ``SafetyBlocked``, ``PromptUnavailable``) and lets
``cli/__init__.py`` print it through :func:`error`; this module is for the messages that are not
raised: a warning in the middle of a run, a refusal that ends it (:func:`fail`).

The text is the message itself, without the level. A second line (a continuation) is indented by
two spaces so it reads as part of the message.
"""

from __future__ import annotations

import sys
from typing import NoReturn, TextIO

from canair.render import ansi


def _lines(level: str, text: str) -> str:
    first, *rest = str(text).split("\n")
    return "\n".join([f"{level}: {first}", *(f"  {line}" if line else "" for line in rest)])


def _emit(level: str, text: str, code: str, file: TextIO | None) -> None:
    stream = file or sys.stderr
    print(ansi.c(_lines(level, text), code, stream=stream), file=stream)


def error(text: str, *, file: TextIO | None = None) -> None:
    """Print ``error: text`` to stderr, in red on a terminal."""
    _emit("error", text, ansi.raw.RED, file)


def warning(text: str, *, file: TextIO | None = None) -> None:
    """Print ``warning: text`` to stderr, in yellow on a terminal."""
    _emit("warning", text, ansi.raw.YELLOW, file)


def note(text: str, *, file: TextIO | None = None) -> None:
    """Print ``note: text`` to stderr, uncoloured."""
    stream = file or sys.stderr
    print(_lines("note", text), file=stream)


def hint(text: str, *, file: TextIO | None = None) -> None:
    """Print ``hint: text`` to stderr with a dim prefix. Put a command to try in the text."""
    stream = file or sys.stderr
    first, *rest = _lines("hint", text).split("\n")
    prefix, _, body = first.partition(": ")
    print(f"{ansi.c(prefix + ':', ansi.raw.DIM, stream=stream)} {body}", file=stream)
    for line in rest:
        print(line, file=stream)


def fail(text: str, *, code: int = 1, file: TextIO | None = None) -> NoReturn:
    """Print ``error: text`` and exit with ``code`` (1 a failure, 2 a usage error or refusal)."""
    error(text, file=file)
    raise SystemExit(code)
