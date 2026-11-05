"""Profile validators: files and parsed data in, errors and warnings out.

Belongs: the checks behind ``canair validate`` (pids/ecus, captures, states, signals, variants,
CAN buses, groups, actions) and the post-write guard the profile editors call.
Not here: the ``canair validate`` parser and its dispatch (``cli/commands/validate/``), and the
edits themselves (``yaml_edit/``).
Imports: the layers below it: the definition packages, ``captures`` and ``analysis`` (the capture
checks), ``interop`` and ``transport`` (rules they own), ``uds``, ``expression`` and ``safety``.
Never ``yaml_edit``, which imports this package, nor ``live`` or ``cli``.

Each check returns data (``report.py``, ``pids.PidsReport``, ``captures.check_captures``) and never
prints or picks an exit code: ``cli/commands/validate/`` does.
"""
