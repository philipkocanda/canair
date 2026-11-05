"""The one place canair asks the user to confirm something.

Every confirmation answers to the same rules (``cli-tui-standards`` skill):

- The question goes to stderr, so stdout stays the command's result (and valid JSON).
- ``[y/N]``: anything but an explicit yes declines, and so does end of input.
- A flag (``--yes``) answers it ahead of time. ``--json`` never does.
- With no terminal on stdin the helper does not wait for an answer that cannot come. It raises
  :class:`PromptUnavailable`, naming the flag that would answer it. The command exits 2.
- A decline prints :data:`ABORTED`, and the caller exits 1.
"""

from __future__ import annotations

import sys

ABORTED = "Aborted — nothing changed."


class PromptUnavailable(Exception):
    """A confirmation is needed, but stdin is not a terminal and no flag answered it."""

    def __init__(self, question: str, flag: str = "--yes"):
        self.question = question
        self.flag = flag
        super().__init__(
            f"{question.rstrip('?.:')} needs confirmation, but stdin is not a terminal "
            f"(pass {flag} to confirm)"
        )


def _read_answer(prompt: str) -> str | None:
    """Write ``prompt`` to stderr and read one line; ``None`` on end of input or Ctrl-C."""
    print(prompt, end="", flush=True, file=sys.stderr)
    try:
        line = sys.stdin.readline()
    except KeyboardInterrupt:
        print(file=sys.stderr)
        return None
    if not line:
        print(file=sys.stderr)
        return None
    return line.strip()


def _require_terminal(question: str, flag: str) -> None:
    isatty = getattr(sys.stdin, "isatty", None)
    if not (isatty and isatty()):
        raise PromptUnavailable(question, flag)


def confirm(question: str, *, yes: bool = False, flag: str = "--yes", danger: bool = False) -> bool:
    """Ask a yes/no question whose default is no.

    ``yes`` is the already-parsed flag. ``danger`` marks a prompt that may change the car or
    the device (the ``!!`` lead-in). Raises :class:`PromptUnavailable` without a terminal.
    """
    if yes:
        return True
    _require_terminal(question, flag)
    lead = "!! " if danger else "  "
    answer = _read_answer(f"{lead}{question} [y/N] ")
    if answer is not None and answer.lower() in ("y", "yes"):
        return True
    print(f"  {ABORTED}", file=sys.stderr)
    return False


def typed_confirm(question: str, word: str, *, yes: bool = False, flag: str = "--yes") -> bool:
    """Ask the user to type ``word`` exactly, for an actuation or a safety bypass."""
    if yes:
        return True
    _require_terminal(question, flag)
    answer = _read_answer(f"!! {question}\n!! Type {word} to continue: ")
    if answer == word:
        return True
    print(f"  {ABORTED}", file=sys.stderr)
    return False
