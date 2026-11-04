"""canair: a vehicle-agnostic CAN/UDS reverse-engineering CLI.

The package root holds only the version. Import from the subpackage that owns the name.
"""

from importlib.metadata import PackageNotFoundError
from importlib.metadata import version as _pkg_version

try:
    __version__ = _pkg_version("canair")
except PackageNotFoundError:  # not installed (e.g. running from a bare source tree)
    __version__ = "0+unknown"
