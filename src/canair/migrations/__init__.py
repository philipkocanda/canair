"""One-shot data/config migration shims (captures, device config, discover rows).

Belongs: one-time rewrites of stored data and config (captures, devices, discover rows).
Not here: anything a normal run needs; a migration runs once, from a ``canair`` command. The DTC
log upgrades itself on first use, so its upgrade is ``dtc/log_upgrade.py``.
Imports: the stores it rewrites (``captures``, ``config``) and the definitions they name.
"""
