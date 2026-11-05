"""``canair pids`` — safely add/update ecus/ parameters and research entries.

A thin, non-interactive wrapper over canair.yaml_edit.pids for the reverse-
engineering workflow. Every edit is:

  1. applied surgically (comments/formatting preserved, YAML re-parsed), then
  2. schema-checked in-process (canair validate pids) — reverted if it fails.

The subcommands and their flags are in ``canair pids --help``.

The examples shown by ``--help`` are in :mod:`.epilog`. The kinds are declared by group:

* :mod:`.entries` — a signal or a whole PID.
* :mod:`.research` — the ``research:`` entries.
* :mod:`.pid_settings` — a PID's own fields (status, period, notes, defaults, support map).
* :mod:`.migrate` — the one-time category migration.
* :mod:`.ecu_facts` — identity, CAN bus, revisions, IOControl, wake and addressing.

Belongs: `canair pids`: the parser, one module per group of kinds and their handlers.
Not here: the edits themselves (`yaml_edit/`) and the checks that revert them (`validation/`).
Imports: `yaml_edit`, `ecus`, `captures` and below.
"""

from .parser import NAME, add_parser, run

__all__ = ["NAME", "add_parser", "run"]
