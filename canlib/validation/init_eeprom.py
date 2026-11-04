"""Warnings for ``init`` commands that wear the ELM327 adapter's EEPROM.

The init is sent on every connect, so a command in it that writes the adapter's
EEPROM is written again each time (see :mod:`canlib.transport.elm327.eeprom`).
``AT SP h`` and ``AT M1`` are not reported: canair sends them as ``AT TP h`` and
``AT M0``, and both WiCAN firmware generations do the same for AutoPID.
"""

from __future__ import annotations

from canlib.transport.elm327.eeprom import ram_only, writes_eeprom


def init_eeprom_warnings(init: object) -> list[str]:
    """One warning per command in ``init`` that still writes the EEPROM as canair sends it."""
    if not isinstance(init, str):
        return []
    return [
        f"profile.yaml: init sends {command}, which writes the adapter's EEPROM on every "
        "connect (rated for about 10,000 writes) — send it once by hand instead "
        "(`canair repl`) and remove it from init"
        for command in (c.strip() for c in init.split(";"))
        if command and writes_eeprom(command) and ram_only(command) == command
    ]
