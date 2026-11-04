"""One-shot data/config migration shims (captures, device config, discover rows).

Belongs: one-time rewrites of stored data and config (captures, devices, the DTC log).
Not here: anything a normal run needs; a migration runs once, from a ``canair`` command.
Imports: the stores it rewrites (``captures``, ``config``, ``dtc``) and ``core``.
"""
