"""The install: which copy is running, its version and build, and whether a newer one exists.

Belongs: the running copy's origin (checkout, tool snapshot), version and build info, the
once-a-day update check behind ``canair update``, and the Python a reinstall uses
(``python_choice``).
Not here: the update command itself (``cli/commands/update.py``) and user config (``config/``).
Imports: ``config``, ``core`` and ``profile`` (to tell which profiles a reinstall would delete).
"""
