"""Formats canair does not own: DBC files, foreign frame logs, WiCAN AutoPID JSON, the docs explorer.

Belongs: reading or writing an external format (``dbc``, ``frame_log``, ``autopid``,
``explorer``) so the rest of canair never learns a foreign shape.
Not here: canair's own file formats (``captures/``, ``ecus/``) and the commands that call these
(``cli/commands/import_*``, ``cli/commands/export.py``, ``cli/commands/wican.py``).
Imports: any package below it. Decoding code does not import it back: an import contract pins
that ``expression`` and ``captures`` stay free of it.
"""
