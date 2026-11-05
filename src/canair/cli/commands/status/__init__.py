"""``canair status`` — show the configured transport, device state, and profile.

The ``--help`` description is :mod:`.description`. :mod:`.parser` declares the flags,
:mod:`.gather` collects the report, :mod:`.probe` holds the read-only probes it uses and
:mod:`.render` prints it.

Belongs: `canair status`: the parser, the report and its printing.
Not here: choosing a transport or probing the firmware generation (`transport/`, `device/`).
Imports: `transport`, `device`, `config`, `install` and below.
"""

from .parser import ALIASES, NAME, add_parser, run

__all__ = ["ALIASES", "NAME", "add_parser", "run"]
