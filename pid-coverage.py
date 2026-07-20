#!/usr/bin/env python3
"""Deprecated shim — use `canair coverage` instead."""
import sys
from canlib.cli import main
if __name__ == "__main__":
    sys.stderr.write("note: pid-coverage.py is deprecated; use 'canair coverage'\n")
    sys.exit(main(["coverage", *sys.argv[1:]]))
