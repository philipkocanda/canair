#!/usr/bin/env python3
"""Deprecated shim — use `canair captures` instead."""
import sys
from canlib.cli import main
if __name__ == "__main__":
    sys.stderr.write("note: query-captures.py is deprecated; use 'canair captures'\n")
    sys.exit(main(["captures", *sys.argv[1:]]))
