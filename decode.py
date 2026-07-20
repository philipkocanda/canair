#!/usr/bin/env python3
"""Deprecated shim — use `canair decode` instead."""
import sys
from canlib.cli import main
if __name__ == "__main__":
    sys.stderr.write("note: decode.py is deprecated; use 'canair decode'\n")
    sys.exit(main(["decode", *sys.argv[1:]]))
