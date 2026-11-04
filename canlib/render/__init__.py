"""Text for the terminal: the colour palette and the shared result formatters.

Belongs: the ANSI palette and its colour gating (``ansi``) and the formatters several packages
print through: hex lines, byte rulers, parameter tables, UDS response blocks (``formatting``).
Not here: a command's own output (its ``render.py``), Textual apps (``tui/``) and decoding itself
(``expression/``, ``uds/``).
Imports: ``expression``, ``privacy``, ``uds`` and ``core``. It sits below ``live`` because live
operations print through it.
"""
