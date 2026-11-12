"""Text for the terminal: the colour palette and the shared result formatters.

Belongs: the ANSI palette and its colour gating (``ansi``), the painter that colours and aligns
text for one stream (``paint``), the one Rich console factory (``console``), the four message
levels (``messages``), the glyphs (``glyphs``), the sweep row markers (``discover``), the one-line
progress status and the speed behind it (``progress``) and the formatters several packages print
through: hex lines, byte rulers, UDS response blocks, sizes, speeds and durations (``formatting``),
the signal tables and a read's printed results (``signal_table``), and the sniffer's per-ID table
(``sniff``), which the CLI and the Textual view both print.
Not here: a command's own output (its ``render.py``), Textual apps (``tui/``) and decoding itself
(``expression/``, ``uds/``).
Imports: ``expression``, ``privacy``, ``uds`` and ``core``. It sits below ``live`` because live
operations print through it.
"""
