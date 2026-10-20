"""``canair search`` — find a string, VIN or byte pattern anywhere in the captures.

Read-only: searches ``captures/`` (and, with ``--defs``, ``ecus/``) and never talks to
a device. Split by domain *kind*, like ``captures``/``correlate``; a bare ``canair
search …`` is shorthand for ``uds``, injected by ``cli.py``'s ``_GROUP_DEFAULTS``.

* :mod:`.uds` — the diagnostic-capture search (domain A), the only kind so far.
  Raw broadcast frames need a different algorithm (an 8-byte frame splits a 17-char
  string across consecutive frames), so a ``can`` kind waits for its own design.

Supporting layers: :mod:`.parser` (argparse), :mod:`.needle` (flag rules → matchers),
:mod:`.haystack` (capture entries → distinct rows), :mod:`.findings` (grouping by
location) and :mod:`.render` (table, paste-ready command, JSON).
"""

from .parser import NAME, add_parser
from .uds import run

__all__ = ["NAME", "add_parser", "run"]
