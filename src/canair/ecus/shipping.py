"""Whether a signal may go into the device profile.

A signal says ``ship: false`` in ``ecus/`` to stay out of the WiCAN AutoPID profile (a verified
duplicate of another signal, say). It is still polled, decoded, shown, captured and analysed. The
flag is one of four gates: the PID's status (:func:`canair.ecus.pids.pid_ships`), ``sensitive``
and ``verified`` are the others, applied by ``canair.interop.autopid.profile.ship_profile``.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

FIELD = "ship"


def param_ships(param: Any) -> bool:
    """Is the signal free to ship: it does not say ``ship: false``."""
    return not (isinstance(param, Mapping) and param.get(FIELD) is False)
