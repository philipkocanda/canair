#!/usr/bin/env python3
# PYTHON_ARGCOMPLETE_OK
"""Deprecated shim — use `canair query` (and `canair scan`/`raw`/`io`/...) instead."""
import sys

from canlib.cli import main

if __name__ == "__main__":
    sys.stderr.write(
        "note: canreq.py is deprecated; use 'canair query' (or scan/raw/io/routines/...).\n"
    )
    sys.exit(main(["query", *sys.argv[1:]]))
