"""The Textual kit, and the arrow-key list picker of the ``canair profile`` command.

The picker (:func:`canair.tui.picker.select_from_list`) is a Rich screen over single-key reads.
The live row views (``io``, ``routines``) share their table and app shape in
:mod:`canair.tui.row_view`.

Belongs: the shared Textual kit (key roles, help, modals, scrolling, status and widgets), the
list picker (``picker``), the live row views (``row_view``) and, in ``apps/``, the Textual apps
the commands open.
Not here: the state an app shows (``live/`` and the package that owns the data), the commands
(``cli/``) and terminal helpers with no Textual in them (``core/terminal.py``).
Imports: every package below the CLI, and Textual. Only this package imports ``textual``.
"""
