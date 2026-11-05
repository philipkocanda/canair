"""``canair pids`` — safely add/update ecus/ parameters and research entries.

A thin, non-interactive wrapper over canair.yaml_edit.pids for the reverse-
engineering workflow. Every edit is:

  1. applied surgically (comments/formatting preserved, YAML re-parsed), then
  2. schema-checked in-process (canair validate pids) — reverted if it fails.

Subcommands:
  upsert-param ECU PID NAME EXPR   Add/update a parameter (expression required)
  rename-param ECU PID OLD NEW     Rename a parameter key (fields preserved)
  rm-param     ECU PID NAME        Remove a parameter
  rename-pid   ECU OLD NEW         Rename a PID key (e.g. B002 -> 22B002)
  rm-pid       ECU PID             Remove a whole PID (header, status, parameters)
  add-research ECU ...             Append a research: entry
  set-status  ECU TARGET STATUS    Update a research item's status (--type/--index disambiguate)
  rm-research  ECU TARGET          Remove a research item (--type/--index disambiguate)
  set-research-notes ECU TARGET [TEXT] Set (or clear) a research item's notes
                                    (--type/--index disambiguate)
  set-research-result ECU TARGET [TEXT] Set (or clear) a research item's result summary
                                    (--type/--index disambiguate)
  set-research-capture-protocol ECU TARGET [TEXT] Set (or clear) how to capture for an item
                                    (--type/--index disambiguate)
  set-pid-status ECU PID STATUS    Set a PID's lifecycle (active|draft|static|slow|ignored)
  set-pid-period ECU PID MS|clear  Set the WiCAN AutoPID polling interval (ms)
  set-pid-notes ECU PID [TEXT]     Set (or clear) a PID's free-text notes
  set-pid-defaults ECU PID ...     Set unit/min/max/verified/source… every parameter inherits
  hoist-defaults [ECU [PID]]       Move fields repeated across a PID's parameters up to the PID
  migrate-categories [ECU]         Replace the retired ha_class field with category
  set-identity ECU FIELD VALUE     Set a curated identity field (e.g. notes)
  rm-identity  ECU FIELD           Remove an identity field (e.g. a redundant duplicate)
  set-can-bus  ECU CODE [CODE ...] Set the physical CAN bus segment(s) (see can_buses.yaml)
  set-iocontrol-ranges ECU RANGE ... Set the 0x2F scan DID ranges (e.g. B000-BFFF)
  set-iocontrol ECU DID ...        Set fields of a curated IOControl actuator entry
  set-wake     ECU --method ...     Set how to rouse a fast-sleeping ECU before reads
  set-addressing ECU ...           Set CAN addressing (mode / extension bytes / fc_id / rx_id)

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
