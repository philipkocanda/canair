"""Generic helpers that need nothing else from canair.

Belongs: code with no knowledge of vehicles, profiles, captures or transports that several
packages share: the path roots and config directory (``paths``), how YAML is read and
round-tripped (``yaml_io``, ``yaml_rt``), the error a rejected edit raises (``edit_errors``,
shared by the editors and by the loaders whose errors they report), hex id ranges, text
scanning, stop signals, and the central diagnostics event log (``eventlog``).
Not here: anything that imports another canair package, which belongs in the package it serves.
The per-ECU response log reads the ECU registry, so it is ``transport/ecu_log.py``.
Imports: the standard library and third-party packages only, never another canair package.
"""
