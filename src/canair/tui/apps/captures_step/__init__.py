"""``canair captures --step``: the capture stepper and comparator.

* :mod:`.app` — the Textual app
* :mod:`.model` — the view model: the frames, the cursor and the diffs, with no Textual in it
* :mod:`.render` — Rich text for one frame, header and separator
* :mod:`.join` — the N-way time join of a stacked multi-PID view
* :mod:`.gaps` — what happened between two frames (a disconnect, a pause)

Belongs: the stepper's screen and the model that ``captures --step`` also prints off a TTY.
Not here: loading the captures (``captures/``), the byte diff itself (``analysis/``) and the
flags (``cli/commands/captures``).
Imports: the kit (``tui``, from ``app`` only), ``captures``, ``analysis``, ``render``, ``uds``
and ``core``. Never ``cli``.

This package imports nothing eagerly. The command imports ``model`` off a TTY, so an ``__init__``
that imported ``app`` would load Textual with it.
"""
