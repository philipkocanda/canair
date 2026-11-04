"""The one error type every profile edit raises when it rejects a change."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager


class EditError(Exception):
    """An edit was rejected: bad input, a missing target, or a failed post-check.

    The editors raise a subclass per file kind (``GroupsEditError``, ``PidsEditError``, ...) so a
    caller can still tell them apart, but ``canlib/cli/__init__.py`` catches this base class once and
    prints it, so no command handles an edit failure itself. A rejected edit never leaves a
    changed file behind.
    """


@contextmanager
def translate(source: type[Exception], target: type[EditError]) -> Iterator[None]:
    """Re-raise ``source`` (a loader's error) as ``target`` with the same message."""
    try:
        yield
    except source as e:
        raise target(str(e)) from None
