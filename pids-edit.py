#!/usr/bin/env python3
"""Deprecated shim — use `canair pids` instead."""
import sys

from canlib.cli import main

if __name__ == "__main__":
    sys.stderr.write("note: pids-edit.py is deprecated; use 'canair pids'\n")
    sys.exit(main(["pids", *sys.argv[1:]]))
