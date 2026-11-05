"""Memory dumps: the local store ``canair memory dump`` writes, and what is computed over it.

A dump is user state, never profile data: it can hold OEM software (copyright) and secrets
such as immobilizer data, so it lives under ``<config dir>/memory/<profile>/<ECU>/`` and
nothing that shares a profile (``contribute``, the blind strip, the explorer, AutoPID)
can reach it. Each dump is a directory: a ``dump.yaml`` manifest and one ``.bin`` per
stretch of memory that was read. An address that was not read is a hole or a skip in the
manifest, never invented bytes.

Belongs: the dump store and manifest (``store``), comparing two dumps (``diff``) and
writing one in a format other tools open (``export``).
Not here: reading the car (``live/memory/``), an ECU's ``memory:`` definitions
(``ecus/memory.py``) and the ``canair memory`` command.
Imports: ``profile``, ``install``, ``uds`` and ``core``.
"""
