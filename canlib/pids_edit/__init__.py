"""Surgical in-place editing of per-ECU PID YAML files.

Split by concern into a package:
  * :mod:`._text`  — shared block-location + field-mutation primitives + the
    YAML re-parse/rollback safety wrapper
  * :mod:`.hits`   — scanner-section editors (routines / iocontrol_discoveries /
    sessions + discovery promotion)
  * :mod:`.params` — parameter / research / identity editors (the RE workflow)
  * :mod:`.support_map` — the per-PID ``support_map:`` declaration editor
  * :mod:`.defaults` — PID-level defaults for parameter fields and hoisting them out of parameters

The full public API is re-exported here so callers that
``from canlib.pids_edit import …`` are unaffected by the split.
"""

from ._text import PidsEditError, find_ecu_file
from .defaults import Hoisted, hoist_defaults, set_pid_defaults
from .hits import (
    EDITABLE_FIELDS,
    IOCONTROL_FIELDS,
    append_iocontrol_discoveries_block,
    append_routines_block,
    append_sessions_block,
    promote_discovery,
    update_iocontrol_field,
    update_routines_field,
)
from .params import (
    add_pid,
    add_research_entry,
    delete_parameter,
    delete_pid,
    delete_research_entry,
    remove_identity_field,
    rename_parameter,
    rename_pid,
    set_can_bus,
    set_identity_field,
    set_iocontrol_scan_ranges,
    set_param_field,
    set_pid_notes,
    set_pid_period,
    set_pid_status,
    set_pid_variable_length,
    set_research_notes,
    set_research_result,
    set_research_status,
    set_response_frames,
    set_wake,
    upsert_parameter,
)
from .support_map import set_support_map

__all__ = [
    "EDITABLE_FIELDS",
    "IOCONTROL_FIELDS",
    "Hoisted",
    "PidsEditError",
    "add_pid",
    "add_research_entry",
    "append_iocontrol_discoveries_block",
    "append_routines_block",
    "append_sessions_block",
    "delete_parameter",
    "delete_pid",
    "delete_research_entry",
    "find_ecu_file",
    "hoist_defaults",
    "promote_discovery",
    "remove_identity_field",
    "rename_parameter",
    "rename_pid",
    "set_can_bus",
    "set_identity_field",
    "set_iocontrol_scan_ranges",
    "set_param_field",
    "set_pid_defaults",
    "set_pid_notes",
    "set_pid_period",
    "set_pid_status",
    "set_pid_variable_length",
    "set_research_notes",
    "set_research_result",
    "set_research_status",
    "set_response_frames",
    "set_support_map",
    "set_wake",
    "update_iocontrol_field",
    "update_routines_field",
    "upsert_parameter",
]
