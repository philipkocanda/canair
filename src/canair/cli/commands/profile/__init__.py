"""``canair profile`` — inspect and manage vehicle profiles.

* :mod:`.parser` — the flags and the ``--help`` text.
* :mod:`.pick` and :mod:`.listing` — a bare ``canair profile``; :mod:`.show` — ``show`` and
  ``path``; :mod:`.use` — ``use`` and the variant and revision pins.
* :mod:`.scaffold` — ``create``, ``adopt`` and ``overlay``; :mod:`.common` — the shared name
  lookup.

Belongs: `canair profile`: the parser and the printing of each kind.
Not here: finding, layering and scaffolding a bundle (`profile/`).
Imports: `profile`, `variants`, `config` and below.
"""

from .parser import ALIASES, NAME, add_parser, run

__all__ = ["ALIASES", "NAME", "add_parser", "run"]
