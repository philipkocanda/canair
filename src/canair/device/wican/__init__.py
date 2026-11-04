"""The WiCAN's HTTP API, firmware probing and protocol-mode switching.

Belongs: ``api`` (the device's HTTP routes), ``firmware`` (which generation it runs, and what
each surface may do on it) and ``mode`` (the protocol mode and the reachability checks).
Not here: the ELM327/SLCAN byte transport (``transport/``).
Imports: ``config`` and ``core``.
"""
