"""Generic helpers that need nothing else from canair.

Belongs: code with no knowledge of vehicles, profiles, captures or transports that several
packages share (how YAML is round-tripped, and later the id-range, text-scan and stop-signal
helpers).
Not here: anything that imports another canlib package, which belongs in the package it serves.
Imports: the standard library and third-party packages only, never another canlib package.
"""
