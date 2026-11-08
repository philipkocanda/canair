"""Memory dumps: the local store ``canair memory`` writes, and what is computed over it.

A dump is user state, never profile data: it can hold OEM software (copyright) and secrets
such as immobilizer data, so it lives under ``<config dir>/memory/<profile>/<ECU>/`` and
nothing that shares a profile (``contribute``, the blind strip, the explorer, AutoPID)
can reach it. Each dump is a directory: a ``dump.yaml`` manifest and the bytes read, written
extent by extent so a killed run keeps what it read. An address that was not read is a hole
or a skip in the manifest, never invented bytes.

Belongs: the dump store, its manifest and incremental writer (``store``), comparing two
dumps (``diff``), writing one in a format other tools open (``export``), and the pure
decisions of a memory image job: its plan and resume state (``job``), the planner
(``plan``), the rate controller (``rate``) and the stability and end checks (``verify``).
Not here: reading the car (``live/memory/``), an ECU's ``memory:`` definitions
(``ecus/memory.py``) and the ``canair memory`` command.
Imports: ``profile``, ``install``, ``transport`` (the ELM327 frame-count ceiling only),
``uds`` and ``core``.
"""
