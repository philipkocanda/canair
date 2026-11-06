"""Vehicle profile resolution.

A *profile* is a self-contained directory bundling one vehicle's data:

    <profile>/
      profile.yaml         profile-wide settings (car_model, init, addressing, ...)
      ecus/                per-ECU definitions — each file is the single source of
                           truth for one ECU (identity, scan_log, dtcs, pids, ...)
      vehicle_states.yaml  vehicle operating-state vocabulary
      can_buses.yaml       physical CAN bus segment vocabulary
      groups.yaml          named capture/monitor selector groups
      actions.yaml         named, guarded make-specific actions (`canair action`)
      variants.yaml        the car's variants (battery, facelift, market) and metadata
      signals/             broadcast signal maps, one <bus>.yaml per CAN bus
      captures/            raw UDS capture files (per date) + can/ frame logs
      references/          external reference material (spreadsheets, other logs)
      out/                 generated WiCAN JSON profiles (optional)

Only ``profile.yaml`` and ``ecus/`` are required; every other member is optional.

Profiles are discovered from several roots (user config dir shadows the repo-bundled ones).
The active profile is chosen by ``--profile`` / ``CANAIR_PROFILE`` / ``default_profile`` in
config, or auto-selected when only one profile exists. A same-named user bundle that declares
``extends:`` **layers** onto the bundled one instead of shadowing it (:func:`profile_layers`):
definitions keep resolving from the read-only base while captures read from every layer and are
written to the user's.

Belongs: the bundle's members (``bundle``), the resolved profile (``model``), finding, resolving
and layering profiles (``discovery``), the refusal to edit a read-only base (``writable``),
creating one (``scaffold``) and what cars call a profile (``names``).
Not here: parsing a bundle's definitions (``ecus/``, ``states/``, ``variants/``, ``broadcast/``),
checking them (``validation/``) or editing them (``yaml_edit/``).
Imports: ``config`` and ``core``.
"""
