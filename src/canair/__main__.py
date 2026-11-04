"""``python -m canair``: the same entry point as the ``canair`` command."""

import sys

from canair.cli import main

if __name__ == "__main__":
    sys.exit(main())
