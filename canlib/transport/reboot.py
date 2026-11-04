"""``--reboot``: restore AutoPID on the WiCAN once a live session has ended."""

from __future__ import annotations

import sys

from canlib.device.wican.api import RebootOutcome, reboot_device
from canlib.transport.config import TransportConfig


def reboot_after_session(transport: TransportConfig) -> RebootOutcome:
    """``--reboot`` once a live session ends, on whichever transport it ran.

    The reboot is a plain HTTP request, so it works the same after a raw
    ``slcan-tcp`` session as after a ``wican-ws`` one. ``elm327-tcp`` reaches no
    WiCAN HTTP API to reboot through, so the flag is reported as skipped rather
    than silently dropped.
    """
    if not transport.host or not transport.is_wican_http:
        print(
            f"note: skipping --reboot: the {transport.type} transport has no WiCAN HTTP API "
            "to reboot the device through",
            file=sys.stderr,
        )
        return "skipped"
    return reboot_device(transport.host)
