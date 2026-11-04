"""``python -m canair.cli``: the same entry point as the ``canair`` command.

The tests and the docs scripts run the CLI this way, without needing the ``canair`` script on PATH.
"""

import sys

from canair.cli import main

if __name__ == "__main__":
    sys.exit(main())
