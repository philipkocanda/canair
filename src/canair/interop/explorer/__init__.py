"""Docs-site profile explorer: export, layout, and captured-signal series.

The export is an **allow-list**: it never carries ``identity:`` values, captures or ``scan_log``,
and every record gets a ``src: {file, line}`` read through ruamel. The mkdocs hook writes the
result as virtual files at docs-build time, and ``docs/assets/explorer/`` only draws it, so the
content rules belong in the exporter, where they are unit-tested. The data view decodes captures
at build time with the shared evaluator in ``series`` (decoded numbers only, never payloads, with
a size budget). Its pure maths is ``docs/assets/explorer/analysis.js``, tested with
``node --test tests/js/analysis.test.js`` (name the file: older Node treats a directory as a
module). Plans: ``plans/2026-10-21-profile-explorer.md`` and
``plans/2026-10-22-explorer-capture-plots.md``.

Belongs: turning a profile into the allow-listed JSON the explorer draws (``export``), placing
ECUs on bus lanes (``layout``) and decoding captured signals at docs-build time (``series``).
Not here: the drawing code (``docs/assets/explorer/``) or the mkdocs hook that writes the files
(``scripts/mkdocs_hooks/explorer.py``).
Imports: ``ecus``, ``captures``, ``expression``, ``profile``, ``privacy`` and ``core``.
"""
