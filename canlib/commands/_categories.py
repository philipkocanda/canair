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
import sys

# ANSI styling for category headers (match the sibling tools: bix, decode, …).
# Emitted only when stdout is a TTY so piped/redirected help stays plain.
_BOLD = "\033[1m"
_CYAN = "\033[96m"
_RESET = "\033[0m"

# Ordered (title, command-names) groups. Order is the display order in --help.
CATEGORIES: list[tuple[str, tuple[str, ...]]] = [
    (
        "Live device",
        (
            "status",
            "read",
            "monitor",
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
    ("Authoring", ("pids", "signals", "ecu", "bus", "states", "groups", "wican", "validate")),
    ("Import / export", ("import", "export")),
    ("Setup", ("profile", "config", "update", "completion")),
]

# Trailing group for commands not placed in any category above.
OTHER_TITLE = "Other"


def categorized_names() -> set[str]:
    """Every command name assigned to a category (excludes the Other bucket)."""
    return {name for _title, names in CATEGORIES for name in names}


def _header(title: str, indent: str) -> str:
    """Render a category header line — uppercased so it stands out even when
    piped, and bold-cyan when stdout is a TTY. Leading blank line separates it
    from the previous group."""
    label = title.upper()
    if sys.stdout.isatty():
        label = f"{_BOLD}{_CYAN}{label}{_RESET}"
    return f"\n{indent}{label}\n"


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
                parts.append(_header(title, indent))
                for sub in group:
                    parts.append(self._format_subcommand(sub))
                    emitted.add(sub.dest)
            leftover = [sub for sub in subactions if sub.dest not in emitted]
            if leftover:
                parts.append(_header(OTHER_TITLE, indent))
                for sub in leftover:
                    parts.append(self._format_subcommand(sub))
        finally:
            self._dedent()

        return "".join(parts)

    def _format_subcommand(self, sub: argparse.Action) -> str:
        """Format one subcommand line, bolding its ``(alias, …)`` hint on a TTY.

        argparse renders an aliased subcommand's invocation as ``name (a, b)``.
        We bold only the parenthesised hint so the shortcuts stand out. The ANSI
        codes are injected *after* argparse has padded the line (column widths are
        computed from the plain text), and escapes have zero display width, so the
        help-text column stays aligned.
        """
        text = super()._format_action(sub)
        if not sys.stdout.isatty():
            return text
        inv = self._format_action_invocation(sub)
        if "(" not in inv or ")" not in inv:
            return text
        hint = inv[inv.index("(") : inv.rindex(")") + 1]
        return text.replace(hint, f"{_BOLD}{hint}{_RESET}", 1)
