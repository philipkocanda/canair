"""Accept the global options (``--profile`` and friends) after the subcommand too.

argparse reads an option only on the parser that declares it, and the global options live on
the top-level parser. So ``canair decode MCU 2102 --profile X`` failed with "unrecognized
arguments", and an option between positionals was worse: ``captures uds --summary --profile X``
took ``X`` as the QUERY. :func:`hoist_global_options` moves each global option that follows the
command to the front, before argparse runs. An option the chosen subcommand declares itself
(``pids upsert-param --revision``) keeps its local meaning and stays where it is.
"""

from __future__ import annotations

import argparse

# Global options that consume a following value. The argv rewrites in ``cli/__init__.py`` run
# before a parser exists and use this set to step over them; a test pins it to the top-level
# parser's own options.
GLOBAL_OPTS_WITH_VALUE = frozenset({"--profile", "--profiles-dir", "--variant", "--revision"})


def skip_leading_globals(argv: list[str]) -> int:
    """The index of the first token after the leading global options."""
    i = 0
    while i < len(argv):
        tok = argv[i]
        if tok in GLOBAL_OPTS_WITH_VALUE:
            i += 2
        elif tok.startswith("--") and "=" in tok:  # --profile=NAME
            i += 1
        else:
            break
    return i


def _subparsers(parser: argparse.ArgumentParser) -> argparse._SubParsersAction | None:
    for action in parser._actions:
        if isinstance(action, argparse._SubParsersAction):
            return action
    return None


def _options_below(parser: argparse.ArgumentParser) -> set[str]:
    """Every option string ``parser`` and the subcommands under it declare."""
    found = set(parser._option_string_actions)
    sub = _subparsers(parser)
    if sub is not None:
        for child in set(sub.choices.values()):
            found |= _options_below(child)
    return found


def hoist_global_options(parser: argparse.ArgumentParser, argv: list[str]) -> list[str]:
    """Move global options found after the command in front of it.

    The command path is followed through the nested subcommands (``pids upsert-param``). Where
    it stops short of a leaf, every subcommand below that point counts as a possible owner, so
    an option is moved only when no parser on the path or below it declares it. Tokens after a
    ``--`` and a trailing option with no value are left for argparse to judge.
    """
    start = skip_leading_globals(argv)
    node, end = parser, start
    local: set[str] = set()
    while end < len(argv):
        sub = _subparsers(node)
        if sub is None or argv[end] not in sub.choices:
            break
        node = sub.choices[argv[end]]
        local |= set(node._option_string_actions)
        end += 1
    if node is parser:
        return argv
    local |= _options_below(node)

    hoisted: list[str] = []
    rest: list[str] = []
    k = end
    while k < len(argv):
        tok = argv[k]
        if tok == "--":
            rest.extend(argv[k:])
            break
        name, eq, _ = tok.partition("=")
        if name in GLOBAL_OPTS_WITH_VALUE and name not in local:
            if eq:
                hoisted.append(tok)
                k += 1
                continue
            if k + 1 < len(argv):
                hoisted.extend(argv[k : k + 2])
                k += 2
                continue
        rest.append(tok)
        k += 1
    if not hoisted:
        return argv
    return [*argv[:start], *hoisted, *argv[start:end], *rest]
