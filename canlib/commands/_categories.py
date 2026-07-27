"""Category grouping for the top-level ``canair --help`` command list.

argparse renders every subcommand as one flat list under ``<command>``. This
module centralizes, in one place, which category each subcommand belongs to so
the top-level help can present the commands in labelled groups (Live device /
Analysis / Authoring / Import·export / Setup) instead of an undifferentiated
wall — mirroring the ``_domain.py`` pattern (one central map, applied uniformly
in ``canlib.cli.build_parser``, rather than hand-wiring 30 modules).

Not every command needs a category: any command absent from :data:`CATEGORIES`
is rendered under a trailing :data:`OTHER_TITLE` group (e.g. the ``bix`` byte
utility). Keys are the subcommand strings (module ``NAME``) — ``"import"``, not
the ``import_`` module filename.
"""

from __future__ import annotations

import argparse

# Ordered (title, command-names) groups. Order is the display order in --help.
CATEGORIES: list[tuple[str, tuple[str, ...]]] = [
    (
        "Live device",
        (
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
        ),
    ),
    (
        "Analysis",
        ("captures", "decode", "correlate", "hunt", "investigate", "coverage", "research"),
    ),
    ("Authoring", ("pids", "signals", "ecu", "wican", "validate")),
    ("Import / export", ("import", "export")),
    ("Setup", ("profile", "config", "update", "completion")),
]

# Trailing group for commands not placed in any category above.
OTHER_TITLE = "Other"


def categorized_names() -> set[str]:
    """Every command name assigned to a category (excludes the Other bucket)."""
    return {name for _title, names in CATEGORIES for name in names}


class CategorizedHelpFormatter(argparse.RawDescriptionHelpFormatter):
    """Top-level help formatter that groups the subcommand list by category.

    Only the subparsers action is special-cased; every other action (options,
    the raw description block) formats exactly as ``RawDescriptionHelpFormatter``
    would. The per-command pseudo-actions are formatted by argparse itself, so
    column alignment and the ``[UDS]``/``[CAN]`` domain tags baked into each
    ``.help`` by ``_domain.apply_domain_tags`` are preserved.
    """

    def _format_action(self, action: argparse.Action) -> str:
        if not isinstance(action, argparse._SubParsersAction):
            return super()._format_action(action)

        subactions = action._choices_actions
        # Render just the "  <command>" header line (no flat subaction dump) by
        # temporarily hiding the choices; restored immediately after.
        action._choices_actions = []
        try:
            parts = [super()._format_action(action)]
        finally:
            action._choices_actions = subactions

        by_name = {sub.dest: sub for sub in subactions}
        emitted: set[str] = set()

        self._indent()
        indent = " " * self._current_indent
        try:
            for title, names in CATEGORIES:
                group = [by_name[n] for n in names if n in by_name]
                if not group:
                    continue
                parts.append(f"\n{indent}{title}:\n")
                for sub in group:
                    parts.append(super()._format_action(sub))
                    emitted.add(sub.dest)
            leftover = [sub for sub in subactions if sub.dest not in emitted]
            if leftover:
                parts.append(f"\n{indent}{OTHER_TITLE}:\n")
                for sub in leftover:
                    parts.append(super()._format_action(sub))
        finally:
            self._dedent()

        return "".join(parts)
