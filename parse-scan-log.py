#!/usr/bin/env python3
"""Deprecated shim — use `canair scan-log` instead."""
import sys
from canlib.cli import main
if __name__ == "__main__":
    sys.stderr.write("note: parse-scan-log.py is deprecated; use 'canair scan-log'\n")
    sys.exit(main(["scan-log", *sys.argv[1:]]))
