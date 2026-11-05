"""``canair config`` — view and manage user configuration.

The ``--help`` text is :mod:`.description`. :mod:`.parser` declares the kinds, :mod:`.show` prints
the effective settings, :mod:`.edit` reads and changes one key, and :mod:`.keys` knows which
keys exist and what values they take.

Belongs: `canair config`: the parser, the settings view and the key checks.
Not here: reading and writing the config file (`config/`).
Imports: `config`, `profile`, `dtc` and below.
"""

from .parser import NAME, add_parser, run

__all__ = ["NAME", "add_parser", "run"]
