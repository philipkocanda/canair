"""Text for the terminal: the colour palette and the shared result formatters.

Belongs: the ANSI palette and its colour gating (``ansi``), the painter that colours and aligns
text for one stream (``paint``), the one Rich console factory (``console``) and the formatters
several packages print through: hex lines, byte rulers, parameter tables, UDS response blocks
(``formatting``), and the sniffer's per-ID table (``sniff``), which the CLI and the Textual view
both print.
Not here: a command's own output (its ``render.py``), Textual apps (``tui/``) and decoding itself
(``expression/``, ``uds/``).
Imports: ``expression``, ``privacy``, ``uds`` and ``core``. It sits below ``live`` because live
operations print through it.
"""
