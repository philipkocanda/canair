#!/usr/bin/env python3
"""Deprecated shim — use `canair wican` instead."""
import sys

from canlib.cli import main

if __name__ == "__main__":
    sys.stderr.write("note: generate-profile.py is deprecated; use 'canair wican'\n")
    sys.exit(main(["wican", *sys.argv[1:]]))
