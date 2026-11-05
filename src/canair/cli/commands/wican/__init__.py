"""``canair wican`` — build and sync the WiCAN device's AutoPID vehicle profile.

Two families of device operations: ``autopid`` (generate the profile from the bundle's
``ecus/`` and sync it with the device) and ``mode`` (show or switch the device's protocol).
Nothing is written until the user asks for it. The text shown under ``--help`` is
:mod:`.epilog`.

* :mod:`.parser` — the command and its two groups; :mod:`.autopid` and :mod:`.mode` declare
  their kinds and hold their handlers.
* :mod:`.generate` — the profile generated from the bundle; :mod:`.sync` — the device's
  AutoPID store; :mod:`.diff` and :mod:`.stats` — the two views of it.
* :mod:`.common` — the Pro and firmware guards every device operation passes.

Belongs: `canair wican`: the parser, the guards and the printing of a sync.
Not here: the AutoPID format (`interop/autopid/`) and the device API (`device/wican/`).
Imports: `interop`, `device`, `ecus`, `config` and below.
"""

from .parser import NAME, add_parser, run

__all__ = ["NAME", "add_parser", "run"]
