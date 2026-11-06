"""``canair io ECU``: the IOControl view of one ECU's actuators.

* :mod:`.app` — the Textual app

Belongs: the IOControl Textual screen.
Not here: the view state and the actuation (``live/actuate/iocontrol.py`` and its siblings),
what a row says (``live/actuate/iocontrol_render.py``) and the flags (``cli/``).
Imports: the kit (``tui``), ``live``, ``render`` and ``core``. Never ``cli``.

This package imports nothing eagerly.
"""
