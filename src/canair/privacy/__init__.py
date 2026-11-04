"""What must never leave the machine, and what to mask on screen.

Belongs: the ``sensitive: true`` rule (``sensitive``: mask on screen, never journal, save or
export) and the contribute-time scrub for personal data in a profile (``pii``: VINs, keys,
vehicle-bound identifiers).
Not here: the upstream PR itself (``contribute``) and where a profile lives (``profile``).
Imports: ``core``, ``uds`` and, for the loaded definitions, ``pids``, ``ecus`` and ``profile``,
which it reads through function-level imports because they import it back to apply the rule.
"""
