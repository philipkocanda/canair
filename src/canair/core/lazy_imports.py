"""Lazy imports for canair's own modules, on Python 3.15 and later (PEP 810).

With lazy imports on, an import at the top of a canair module binds a placeholder, and Python
loads the module when code first uses the name. A command then loads what it runs, not every
module the parser build reaches: building the parser takes about 110 ms instead of 270 ms
(measured on 3.15). On Python before 3.15, :func:`enable` does nothing and every import runs
as written.

Only the imports that canair's modules make are lazy. Third-party packages import their own
dependencies as usual: with every import lazy, pytest fails with ``ImportCycleError``.

What lazy imports change (PEP 810):

- An import inside a function or a ``try`` block stays eager.
- A module that fails to import raises where its name is first used, not at start-up.
- ``from a import name`` reads ``a.name`` when the name is first used. A test that patches
  ``a.name`` before then hands the patch to the importing module for good, so patch the name
  in the module that uses it.
"""

from __future__ import annotations

import sys

PACKAGE = "canair"


def _imported_by_canair(importer: str, name: str, fromlist: object = None) -> bool:
    return importer == PACKAGE or importer.startswith(PACKAGE + ".")


def enable() -> bool:
    """Make the top-level imports of canair modules lazy from now on.

    Returns False when this Python has no lazy imports (before 3.15).
    """
    set_mode = getattr(sys, "set_lazy_imports", None)
    set_filter = getattr(sys, "set_lazy_imports_filter", None)
    if set_mode is None or set_filter is None:
        return False
    set_filter(_imported_by_canair)
    set_mode("all")
    return True
