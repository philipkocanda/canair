"""Everything that writes a profile's YAML: the editors, their shared write path and helpers.

The ``canair`` authoring commands (``pids``, ``signals``, ``states``, ``groups``, ``variants``,
``action``, ``ecu``) call these editors; nothing else in canair rewrites a definition file. The
package imports nothing at load time, so ``import canair.core.yaml_rt`` costs nothing extra.

Mechanism, shared by every editor
    :mod:`.errors`      ``EditError``, the one error a rejected edit raises
    :mod:`.commit`      ``commit_text``: write atomically, re-check, revert on any failure
    :mod:`.doc`         ``Target`` and ``edit_doc``: open one section of one file (ruamel)
    :mod:`.targets`     the router: which file and section each kind of edit belongs to
    :mod:`.ruamel_ops`  comment-safe helpers on ruamel trees (rename, remove, place, flow lists)
    :mod:`canair.core.yaml_rt`  how YAML is rendered: round-trip settings, notes, quoting
    :mod:`.text`        text-surgery primitives and the ``edit_ecu_text`` entry point
    :mod:`.ecufile`     finding an ECU's file; the schema check; the cache to drop
    :mod:`.echo`        the confirmation line every edit prints

Editors, one per file kind
    :mod:`.groups` :mod:`.actions` :mod:`.states` :mod:`.variants` :mod:`.signals`
        a profile's ``groups.yaml``, ``actions.yaml``, ``vehicle_states.yaml``,
        ``variants.yaml`` and ``signals/<bus>.yaml``
    :mod:`.ecu` :mod:`.revisions`
        ``ecus/``: registration, addressing, scan log, revisions, gates, overrides (ruamel)
    :mod:`.pids`
        ``ecus/``: signals, PIDs, research, identity, IOControl, wake, scanner hits (text)
    :mod:`.state_rename` :mod:`.variant_refs`
        a rename that also rewrites every reference in the profile's history

A new editor declares a ``Target`` beside its rules, registers it in :mod:`.targets`, and writes
through ``edit_doc`` (or ``edit_ecu_text``). It never opens the file itself.
"""
