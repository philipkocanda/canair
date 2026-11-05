"""What must never leave the machine, and what to mask on screen.

Belongs: the ``sensitive: true`` rule (``sensitive``: mask on screen, never journal, save or
export).
Not here: the contribute-time scan for personal data in a profile (``contribute/pii.py``),
reading the flag off a definition (``ecus/sensitivity.py``) and where a profile lives
(``profile``).
Imports: the loaded definitions (``ecus``, ``profile``), ``uds`` and ``core``.
"""
