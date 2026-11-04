"""Keeping an ELM327 adapter's EEPROM from wearing out.

An ELM327 (and an STN chip in ELM mode, such as the WiCAN Pro's) stores its default
protocol and a few settings in on-chip EEPROM rated for roughly 10,000 writes. Two
things write it on an ordinary connect:

- ``AT SP h`` sets the protocol *and saves it* as the new default, "no matter what
  the AT M0/M1 setting is" (ELM327 datasheet). ``AT TP h`` selects the same protocol
  without saving it.
- With the memory function on (``AT M1``, the default after a reset on the WiCAN
  Pro's chip and on many clones), a protocol found valid by a request is stored as
  the new default, even after ``AT TP``.

canair sends the profile's ``init`` on every connect, reconnect and fallback attempt,
so a scripted loop of one-shot reads could use up the rating within a day. The
connect therefore turns the memory function off after every reset and sends
protocol selections as ``AT TP``. WiCAN firmware v6 rewrites the same two commands
on its own pass-through; 4.x and generic adapters do not, so canair does it itself.

Because ``AT TP`` is not saved, a reset forgets it: a reset the ``init`` itself sends
is followed by the memory function and the protocol selection again.

Other commands that write the EEPROM (``AT PP``, ``AT SD``, ``AT CV``, ``ST WBR``,
``ST SAVCAL``, ``AT SP 00``) have no RAM-only form. They are sent as written, and
``canair validate`` warns about them in an ``init``.
"""

from __future__ import annotations

import re
from typing import Final

from canair.transport.elm327.commands import RESET_COMMANDS, canonical

MEMORY_OFF: Final = "ATM0"

# `AT SP h`, `AT SP Ah` and `AT SP hA`. `AT SP 00` (erase the stored protocol) has
# no `AT TP` counterpart, so it is not matched.
_SET_PROTOCOL_RE: Final = re.compile(r"^ATSP(?:A([0-9A-C])|([0-9A-C])(A?))$")

# Writes with no RAM-only form, matched as prefixes. ATPPS (the summary) only reads.
_PERSISTENT_PREFIXES: Final = ("ATSD", "ATCV", "STWBR", "STSAVCAL")


def ram_only(command: str) -> str:
    """``command`` in a form that does not write the EEPROM; unchanged when it never did.

    ``AT SP`` accepts its automatic flag on either side of the digit, ``AT TP`` is
    documented only as ``AT TP Ah``, so the flag always goes first.
    """
    cu = canonical(command)
    match = _SET_PROTOCOL_RE.match(cu)
    if match:
        if match.group(1) is not None:
            return f"ATTPA{match.group(1)}"
        return f"ATTP{'A' if match.group(3) else ''}{match.group(2)}"
    if cu == "ATM1":
        return MEMORY_OFF
    return command


def writes_eeprom(command: str) -> bool:
    """Whether ``command``, sent as written, writes or can write the adapter's EEPROM."""
    cu = canonical(command)
    if cu.startswith("ATSP") or cu == "ATM1":
        return True
    if cu.startswith("ATPP") and not cu.startswith("ATPPS"):
        return True
    return cu.startswith(_PERSISTENT_PREFIXES)


def init_commands(init: str) -> list[str]:
    """The profile's ``;``-separated ``init`` as canair sends it after its own reset.

    The memory function goes off first, and again after any reset the init itself
    sends, followed by the protocol selected so far (an ``AT TP`` does not survive
    the reset the way a saved ``AT SP`` did); every protocol selection becomes its
    RAM-only form.
    """
    commands = [MEMORY_OFF]
    protocol: str | None = None
    for element in init.split(";"):
        command = element.strip()
        if not command:
            continue
        sent = ram_only(command)
        commands.append(sent)
        if canonical(sent).startswith("ATTP"):
            protocol = sent
        if canonical(command) in RESET_COMMANDS:
            commands.append(MEMORY_OFF)
            if protocol is not None:
                commands.append(protocol)
    return commands
