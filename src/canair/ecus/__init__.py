"""The ECU registry and the per-ECU PID definitions.

The registry derives from the profile's ``ecus/`` files (keyed by short name, with a ``tx_id``
and an ``identity:`` block). :mod:`canair.uds.addressing` resolves the CAN *response* address
per ECU; captures hold it as hex, and the helpers here map it to the name.

Belongs: the ECU registry, its response addresses and names (``registry``, ``names``,
``protocol``), and ECU, PID, ``dtcs:`` and ``wake:`` definitions, CAN buses, DID families,
categories.
Not here: the bundle (``profile``), checks (``validation``), edits (``yaml_edit``), scans (``dtc``).
Imports: ``profile``, ``variants``, ``safety``, ``uds``, ``core``.
"""
