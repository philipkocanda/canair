"""The adapter as a managed device, not a byte pipe.

Belongs: the WiCAN's HTTP API, firmware generation and protocol mode (``wican``), and the
connection mutex that keeps two sessions off its one connection (``lock``, ``lock_watchdog``).
Not here: moving CAN bytes (``transport/``) or building the AutoPID profile (``interop/``).
Imports: ``config`` and ``core``. It never imports ``transport``, which imports it.
"""
