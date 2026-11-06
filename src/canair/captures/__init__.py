"""The capture store: the file format, saving, reading back, the journal and the merge driver.

On disk, one JSON file per date (``captures/YYYY-MM-DD.json``), append-only session logs. No
reader or writer opens one directly: the format seam is ``io``, which folds repeated polls on
write and expands them on read, and nothing hand-writes or hand-edits a capture (record with
``--save``, import with ``canair import uds``, remove with ``captures uds --delete``). Rules a
change keeps:

* A capture's ``rx`` is the ECU's CAN *response* address (``ecus/registry.py::rx_for_tx``), not
  an ECU name. A discover capture's ``rx`` is ``broadcast``, and each responder records its
  probed ``tx`` as well.
* Every ``--save`` path records ``transport`` from the live client
  (``transport/stats.py::client_transport``), never by resolving the config again: that ignores
  ``--transport``, ``--wican`` and the connect-time fallback.
* ``elapsed_ms`` is the wall-clock round trip of a single per-DID read: a relative speed signal
  that is comparable only within one session's ``transport``.
* A multi-frame reply shorter than its ISO-TP First Frame length is rejected when it is read
  (``uds/parse.py``), so a misaligned payload is never saved.
* Same-day recordings from two machines union-merge through the git merge driver (``merge``).
  Git never loads a driver from a tracked file, so each clone runs
  ``canair captures merge-driver --install`` once.

Belongs: capture files and entries: building (``build``), saving (``save``, ``oneshot``), editing
(``session_edit``), format, store, layers, journal, merge, grouping, timelines.
Not here: other computing over captures (``analysis/``), the DTC log (``dtc/``), flag parsing.
Imports: the definitions (``states``, ``ecus``, ``variants``, ``profile``, ``privacy``) and below.
"""
