#!/usr/bin/env python3
"""Deprecated shim — use `canair validate captures` instead."""
import sys
from canlib.cli import main
if __name__ == "__main__":
    sys.stderr.write("note: validate-captures.py is deprecated; use 'canair validate captures'\n")
    sys.exit(main(["validate", "captures", *sys.argv[1:]]))
