#!/usr/bin/env python3
"""Deprecated shim — use `canair research` instead."""
import sys
from canlib.cli import main
if __name__ == "__main__":
    sys.stderr.write("note: research.py is deprecated; use 'canair research'\n")
    sys.exit(main(["research", *sys.argv[1:]]))
