#!/usr/bin/env python3
"""Deprecated shim — use `canair validate pids` instead."""
import sys
from canlib.cli import main
if __name__ == "__main__":
    sys.stderr.write("note: validate-pids.py is deprecated; use 'canair validate pids'\n")
    sys.exit(main(["validate", "pids", *sys.argv[1:]]))
