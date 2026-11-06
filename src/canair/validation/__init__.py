"""Profile validators: files and parsed data in, errors and warnings out.

Belongs: the checks behind ``canair validate`` (pids/ecus, captures, states, signals, variants,
CAN buses, groups, actions) and the post-write guard the profile editors call.
Not here: the ``canair validate`` parser and its dispatch (``cli/commands/validate/``), and the
edits themselves (``yaml_edit/``).
Imports: the layers below it: the definition packages, ``captures`` and ``analysis`` (the capture
checks), ``interop`` and ``transport`` (rules they own), ``uds``, ``expression`` and ``safety``.
Never ``yaml_edit``, which imports this package, nor ``live`` or ``cli``.

Checks a change to the profile data is likely to meet, beyond the schema:

* every target errors on a repeated YAML key (the loaders keep the later value silently);
* ``pids`` errors on a duplicate *shipped* signal name across PIDs, a hand-built word whose lower
  byte is read signed, an ``identity:`` field outside the schema, and an expression the
  firmware's checker refuses; it warns on synonymous identity fields that hold the same value;
* ``states`` resolves each ``when:`` predicate's ``ECU.PARAM`` against the registry
  (``states/refs.py``), the only place a renamed signal is caught;
* ``captures`` warns on states outside the vocabulary, echo mismatches, cross-ECU misfiles,
  unsplit multi-DID replies, untimed payloads and degraded transports. ``--max-untimed`` and
  ``--max-untagged`` are the CI ratchets: they fail when the count grows.

Each check returns data (``report.py``, ``pids.PidsReport``, ``captures.check_captures``) and never
prints or picks an exit code: ``cli/commands/validate/`` does.
"""
