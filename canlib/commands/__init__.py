"""Subcommand modules for the ``canair`` CLI.

Each command module exposes:

- ``NAME``       — the subcommand name (str)
- ``add_parser(subparsers)`` — register an argparse subparser; must call
  ``parser.set_defaults(func=run)``
- ``run(args)``  — execute the command; return an int exit code or None

New commands are registered in :data:`COMMAND_MODULES` below (import order
determines help ordering).
"""

from __future__ import annotations

from importlib import import_module
from types import ModuleType

# Subcommand module names, in help-display order.
COMMAND_NAMES: list[str] = [
    # live device
    "status",
    "query",
    "scan",
    "discover",
    "raw",
    "sniff",
    "io",
    "routines",
    "identity",
    "dtc",
    "repl",
    # offline analysis
    "captures",
    "decode",
    "coverage",
    "research",
    # authoring / maintenance
    "pids",
    "validate",
    "wican",
    "ecu",
    "profile",
    "config",
    # utilities
    "bix",
    "completion",
]


def iter_command_modules() -> list[ModuleType]:
    """Import and return every registered command module."""
    return [import_module(f"canlib.commands.{name}") for name in COMMAND_NAMES]
