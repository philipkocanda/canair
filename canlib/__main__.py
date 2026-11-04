"""``python -m canlib``: the same entry point as the ``canair`` command."""

import sys

from canlib.cli import main

if __name__ == "__main__":
    sys.exit(main())
