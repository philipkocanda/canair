"""Decode captured UDS payloads using PID signal definitions — the ``decode`` command.

Takes an ECU+PID, loads all matching captures, applies WiCAN expressions from the
YAML PID definitions, and reports how each decoded *signal value* behaves across
the full capture history. Value-centric and focused on validating expressions —
for payload/byte-level views (hex, byte-diff, dedup, cross-ECU, dates) use
``canair captures`` instead.

This module is only the command's registration surface. The work lives in
submodules, in the order a run passes through them:

* :mod:`.parser`  — the argparse surface and the ``--help`` examples.
* :mod:`.entry`   — argument-combination guards, then fan out over the QUERY's targets.
* :mod:`.query`   — QUERY expansion, capture scoping, ``--try``/``--corr`` inputs.
* :mod:`.one`     — the per-PID pipeline: resolve signals, decode, emit view(s).
* :mod:`.calc`    — the numeric work (series extraction, pairing, mirrors).
* :mod:`.render`  — every human/JSON/CSV rendering.
* :mod:`.plot`    — the ``--plot`` model, and :mod:`.plot_tui` its Textual app.
"""

from .entry import run
from .parser import ALIASES, NAME, add_parser

__all__ = ["ALIASES", "NAME", "add_parser", "run"]
