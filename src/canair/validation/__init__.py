"""Profile validators: files and parsed data in, errors and warnings out.

Belongs: the checks behind ``canair validate`` (pids/ecus, captures, states, signals, variants,
CAN buses, groups, actions) and the post-write guard the profile editors call.
Not here: the ``canair validate`` parser and its dispatch (``commands/validate/``), and the edits
themselves (``yaml_edit/``).
Imports: the definition packages, ``uds``, ``expression`` and ``safety``. Never ``commands``,
``modes`` or ``cli``.

Known debt (plans/2026-11-04-package-layout-and-layering.md): the ``_run_*`` functions still
print and return an exit code from here, ``pids.py`` is over the size limit, and ``pids.py``
imports ``yaml_edit`` for one constant while ``yaml_edit`` imports this package.
"""
