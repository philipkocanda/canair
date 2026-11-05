"""``canair ecu`` — list ECUs, or show one ECU's details and PID stats.

With no argument it prints a plain, pipeable list of every ECU in the active profile's
``ecus/`` files. Given an ECU name, alias or hex TX/RX id it prints that ECU's identity
fields plus reverse-engineering stats and a per-PID breakdown. Its other kinds add, rename
and edit an ECU, and edit its revisions. The ``--help`` examples and column legend are in
:mod:`.epilog`.

* :mod:`.parser` — the group and its ``show`` kind; :mod:`.add`, :mod:`.rename` and
  :mod:`.revisions` declare their own kinds.
* :mod:`.show` — the dispatch of ``show``; :mod:`.records` builds what it shows, and
  :mod:`.list_view`, :mod:`.detail` and :mod:`.pids_view` print it.
* :mod:`.edit` — the ``$EDITOR`` escape hatch.

Belongs: `canair ecu`: the parser, the views and the ECU edits.
Not here: the ECU definitions (`ecus/`) and the YAML edits (`yaml_edit/`).
Imports: `ecus`, `captures`, `dtc`, `yaml_edit` and below.
"""

from .parser import NAME, add_parser
from .show import run

__all__ = ["NAME", "add_parser", "run"]
