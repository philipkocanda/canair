"""canair: a vehicle-agnostic CAN/UDS reverse-engineering CLI.

The package root holds only the version. Import from the subpackage that owns the name.
"""

from __future__ import annotations


def __getattr__(name: str) -> str:
    """``canair.__version__``, read from the installed package's metadata on first use.

    Reading the metadata costs about 15 ms (it loads ``importlib.metadata``), and only
    ``--version``, ``update`` and the update notice need the version, so it is not read when
    canair loads.
    """
    if name != "__version__":
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    from importlib.metadata import PackageNotFoundError, version

    try:
        found = version("canair")
    except PackageNotFoundError:  # not installed (e.g. running from a bare source tree)
        found = "0+unknown"
    globals()["__version__"] = found
    return found
