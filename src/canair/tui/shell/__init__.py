"""The application shell: a menu bar, a command palette, a zoned status line, a bottom line.

An app that adopts the shell describes its commands once, as a :class:`~.commands.Catalogue`,
and the shell draws the rest from it: the menus with each item's key and state, the ``ctrl+p``
palette, and the help sheet's rows. The app also hands over its status *zones* (the facts it
reports, grouped, each ranked) and its *context* (what has focus and its keys); the shell lays
them out for the terminal's width and puts messages on the bottom line.

* :mod:`.commands` — :class:`~.commands.Command`, :class:`~.commands.Menu`, the catalogue
* :mod:`.menubar` — the menu bar, the width ladder of its titles, the dropdown
* :mod:`.palette` — the command palette's provider over the catalogue
* :mod:`.statusline` — the zones, their layout and the clickable status line
* :mod:`.contextline` — the bottom line: a message, or the context and the routes to commands
* :mod:`.messages` — the message policy (an error stays until the next key) and the history
* :mod:`.app` — :class:`~.app.ShellMixin`, which wires the pieces into an app

The design and the reasons are in ``plans/2026-11-12-monitor-app-redesign.md``; the build is
``plans/2026-11-12-monitor-app-shell.md``.

Belongs: the frame around an app's content, with no knowledge of any one app.
Not here: what an app's menus hold and what its zones say (the app's own package in
``tui/apps/``), the widgets of the old one-bar layout (``tui/status.py``, ``tui/flash.py``).
Imports: the rest of the kit (``tui``), ``render`` and Textual.

This package imports nothing eagerly.
"""
