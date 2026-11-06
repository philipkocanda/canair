"""Flag helpers shared by several subcommands, and the layout of the top-level help.

Belongs: argparse surfaces more than one command declares (scope, join, ``--notation``,
``--wake``, transport selection, raw CAN log inputs, hex id arguments, subcommand groups, the
QUERY with ECU aliases resolved) and the grouping
and domain tags of ``canair --help``, each declared once so its defaults cannot drift.
Not here: a flag only one command has (its own parser) and what a flag selects (the package
that owns the behaviour).
Imports: every package below ``cli``. Never ``cli.commands``, which imports these.
"""
