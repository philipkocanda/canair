"""Docs-site profile explorer: export, layout, and captured-signal series.

Belongs: turning a profile into the allow-listed JSON the explorer draws (``export``), placing
ECUs on bus lanes (``layout``) and decoding captured signals at docs-build time (``series``).
Not here: the drawing code (``docs/assets/explorer/``) or the mkdocs hook that writes the files
(``scripts/mkdocs_hooks/explorer.py``).
Imports: ``ecus``, ``captures``, ``expression``, ``profile``, ``privacy`` and ``core``.
"""
