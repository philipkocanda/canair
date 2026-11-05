"""Shared plumbing of the commands that edit a profile's YAML (``pids``, ``states``, ``groups``,
``signals``, ``variants``, ``action``, ``ecu``).

Each of them used to carry its own copy of "refuse a read-only profile, run the editor, turn its
failure into a message, confirm where the file landed". This module is that sequence once, so a
command body is just the editor call and the confirmation text.

An editor that rejects a change raises :class:`canair.core.edit_errors.EditError`; nothing here
catches it. ``src/canair/cli/__init__.py`` prints it (``  Error: …``, exit 1) for every command alike, and the
file is already back to what it was.
"""

from __future__ import annotations

import argparse
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

from canair.yaml_edit.commit import schema_validation
from canair.yaml_edit.echo import echo_edit


def add_edit_args(parser: argparse.ArgumentParser) -> None:
    """The ``--dir`` and ``--no-validate`` arguments of the ECU-file editors."""
    parser.add_argument(
        "--dir", type=Path, default=None, help="ecus/ directory (default: active profile)"
    )
    parser.add_argument(
        "--no-validate", action="store_true", help="Skip the post-edit schema validation gate"
    )


@contextmanager
def edit_scope(args: argparse.Namespace | None = None) -> Iterator[None]:
    """Guard a write: refuse a read-only (layered) profile, and apply ``--no-validate``.

    An explicit ``--dir`` names another profile's files, so the active profile's read-only
    check does not apply to it.
    """
    from canair.profile import require_writable_definitions

    if getattr(args, "dir", None) is None:
        require_writable_definitions()
    with schema_validation(not getattr(args, "no_validate", False)):
        yield


def run_edit(
    do: Callable[[], Path],
    message: str | Callable[[Path], str],
    *,
    args: argparse.Namespace | None = None,
) -> int:
    """Run an editor inside :func:`edit_scope`, then confirm the write.

    ``do`` calls the editor and returns the path it wrote; ``message`` (or a function of that
    path) is the confirmation, printed with the full path and the install-snapshot warning.
    """
    with edit_scope(args):
        path = do()
    echo_edit(message if isinstance(message, str) else message(path), path)
    return 0


def subcommand_dispatch(attr: str, default: Callable[[Any], int]) -> Callable[[Any], int]:
    """A command's ``run``: call the sub-command stored under ``attr``, else ``default``."""

    def run(args: Any) -> int:
        func = getattr(args, attr, None)
        return default(args) if func is None else func(args)

    return run


def set_or_clear(value: object) -> str:
    """``"set"`` for a value, ``"cleared"`` for an empty one (a confirmation's verb)."""
    return "set" if value else "cleared"


def clear_sentinel(text: str) -> str | None:
    """``None`` when ``text`` is the word ``clear``, else ``text`` (``period 2500|clear``)."""
    return None if text.strip().lower() == "clear" else text
