"""The install: which copy is running, its version and build, and whether a newer one exists.

Belongs: the running copy's origin (checkout, tool snapshot), version and build info, and the
once-a-day update check behind ``canair update``.
Not here: the update command itself (``commands/update.py``) and user config (``config/``).
Imports: ``config``, ``core`` and ``profile`` (to tell which profiles a reinstall would delete).
"""
