"""``canair status`` — show the configured transport, device state, and profile.

The ``--help`` description is :mod:`.description`. :mod:`.parser` declares the flags,
:mod:`.gather` collects the report, :mod:`.probe` holds the read-only probes it uses and
:mod:`.render` prints it.

It resolves the whole candidate list (``resolve_transport_candidates``) and runs the same
connect-time fallback probe as a live command, so it reports the device that ``read`` and
``monitor`` would use and names what it fell back from. Never resolve only the first candidate
here: that is what once made it call a working setup dead because the selected device was down.
``--no-fallback`` pins the selected device.

Belongs: `canair status`: the parser, the report and its printing.
Not here: choosing a transport or probing the firmware generation (`transport/`, `device/`).
Imports: `transport`, `device`, `config`, `install` and below.
"""

from .parser import ALIASES, NAME, add_parser, run

__all__ = ["ALIASES", "NAME", "add_parser", "run"]
