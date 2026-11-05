"""``canair decode --plot``: the interactive signal explorer.

* :mod:`.app` — the Textual app
* :mod:`.model` — the view model: capture-derived data and view state, with no Textual in it
* :mod:`.draw` — the braille canvas and the info lines (pure)

Belongs: the explorer's screen and the model that ``decode --plot`` also prints as one frame off
a TTY.
Not here: the byte interpretation it draws on (``expression/inspect_bytes.py``), loading the
captures (``captures/``) and the flags (``cli/commands/decode``).
Imports: the kit (``tui``, from ``app`` only), ``analysis``, ``expression`` and ``core``. Never
``cli``.

This package imports nothing eagerly. The command imports ``model`` off a TTY, so an ``__init__``
that imported ``app`` would load Textual with it.
"""
