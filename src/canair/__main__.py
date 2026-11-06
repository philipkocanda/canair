"""``canair`` and ``python -m canair``: turn on lazy imports, then run the CLI.

The ``canair`` command starts here, not in :mod:`canair.cli`: lazy imports must be on before
``canair.cli`` loads, or its imports run eagerly (:mod:`canair.core.lazy_imports`).
"""

import sys

from canair.core import lazy_imports

lazy_imports.enable()

from canair.cli import main  # noqa: E402

if __name__ == "__main__":
    sys.exit(main())
