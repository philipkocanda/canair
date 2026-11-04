"""``python -m canlib.cli``: the same entry point as the ``canair`` command.

Kept because a git merge driver installed by ``canair captures merge-driver --install`` records
this module name in the clone's git config.
"""

import sys

from canlib.cli import main

if __name__ == "__main__":
    sys.exit(main())
