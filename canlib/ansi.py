"""One home for canair's hand-rolled ANSI escape codes and the "should this be
coloured?" policy.

Historically each command module declared its own palette (``_BOLD``/``_DIM``/…)
and, in a few places, its own ``_use_color``/``_c`` helpers. The codes were
identical everywhere — this leaf consolidates them — but the *gating* was only
implemented in the four modules that thought to add it, which is why most
commands used to leak escapes into a pipe and none honoured ``NO_COLOR``.

Two ways to reach the palette:

- **Gated (the default):** ``ansi.BOLD`` / ``ansi.DIM`` / … resolve to the escape
  code when :func:`use_color` says colour is on for ``sys.stdout``, and to an
  empty string when it isn't. Any ``f"{ansi.BOLD}text{ansi.RESET}"`` pattern in a
  command becomes ``"text"`` when piped or when ``NO_COLOR`` is set, with no
  per-call wrapper. This is what commands should use.
- **Raw:** the codes at ``ansi.raw.BOLD`` etc. bypass the gate — used by
  :func:`c` / :func:`cerr` internally, by tests, and by the rare caller that
  must produce the escape code deliberately (e.g. writing to a captured buffer
  that will be gated at print time).

The policy is: ``FORCE_COLOR`` wins, then ``NO_COLOR``, then the stream's own
TTY-ness — the widely-honoured convention. This module imports only stdlib; every
other module in ``canlib/`` may import it.
"""

from __future__ import annotations

import os
import sys
from typing import TextIO


class _RawPalette:
    """The seven codes actually in use across the tree, in one place.

    Read via :attr:`raw` — this is what :func:`c` emits when gating is on, and
    what tests assert against. Consumers writing coloured output should use the
    module-level gated names (``ansi.BOLD`` etc.) instead.
    """

    BOLD = "\033[1m"
    DIM = "\033[2m"
    RED = "\033[91m"
    GREEN = "\033[92m"
    YELLOW = "\033[93m"
    CYAN = "\033[96m"
    RESET = "\033[0m"


raw = _RawPalette()

# The set of gated attribute names (mirrors :class:`_RawPalette`).
_GATED_NAMES = frozenset(("BOLD", "DIM", "RED", "GREEN", "YELLOW", "CYAN", "RESET"))


def use_color(stream: TextIO | None = None) -> bool:
    """Return ``True`` when output on *stream* should carry ANSI escapes.

    Resolution order (matches the ``NO_COLOR``/``FORCE_COLOR`` conventions):

    1. ``FORCE_COLOR`` set to any non-empty value → yes (even into a pipe).
    2. ``NO_COLOR`` set to any non-empty value → no (even on a TTY).
    3. Otherwise, ``stream.isatty()``.

    ``stream`` defaults to ``sys.stdout``. Pass ``sys.stderr`` for a stderr-gated
    check (the :func:`cerr` helper does this for you).
    """
    if os.environ.get("FORCE_COLOR"):
        return True
    if os.environ.get("NO_COLOR"):
        return False
    if stream is None:
        stream = sys.stdout
    isatty = getattr(stream, "isatty", None)
    return bool(isatty and isatty())


def __getattr__(name: str) -> str:
    """Module-level ``__getattr__`` (PEP 562): make ``ansi.BOLD`` etc. gated.

    Resolves to the raw escape code when :func:`use_color` says colour is on for
    ``sys.stdout``, and to an empty string otherwise. That means a plain
    ``f"{ansi.BOLD}text{ansi.RESET}"`` in a command emits ``"text"`` when piped or
    when ``NO_COLOR`` is set, with no per-call wrapper needed.

    Other attributes (``raw``, ``c``, ``cerr``, ``use_color``) resolve normally
    because they exist at the module level and ``__getattr__`` is a fallback.
    """
    if name in _GATED_NAMES:
        return getattr(raw, name) if use_color() else ""
    raise AttributeError(f"module 'canlib.ansi' has no attribute {name!r}")


def c(text: str, code: str, *, stream: TextIO | None = None) -> str:
    """Wrap *text* in ANSI *code* + reset, or return it plain when colour is off.

    Gated on :func:`use_color` for *stream* (defaults to ``sys.stdout``).

    Accepts either a raw code (``ansi.raw.CYAN``) or a gated one (``ansi.CYAN``).
    When gating is off, both resolve to a no-op — the gated attribute is already
    ``""``, and this function's own gate check independently returns *text*
    unwrapped, so :attr:`raw` codes stay suppressed too.
    """
    return f"{code}{text}{raw.RESET}" if use_color(stream) else text


def cerr(text: str, code: str) -> str:
    """Like :func:`c`, but gated on ``sys.stderr`` (warnings go to stderr)."""
    return c(text, code, stream=sys.stderr)
