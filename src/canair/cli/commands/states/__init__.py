"""``canair states`` — list and edit the profile's vehicle-state vocabulary.

The vehicle-state vocabulary lives in ``profiles/<name>/vehicle_states.yaml`` —
a hand-curated, ordered list of the car's power/operating states (the analogue
of ``can_buses.yaml`` for the state axis). A capture session's ``vehicle_states``
and each PID/DID/research entry's ``vehicle_states`` draw from this vocabulary,
and states with a ``when:`` predicate are auto-suggested from decoded PID values.

Several predicates are true at once by design — driving a running EV matches both
``READY`` and ``DRIVING``. An ``implies:`` list declares which of them is the more
*specific* reading (``DRIVING implies [READY]``), so a live view that must name one
state names the specific one. See :func:`canair.states.most_specific_states`.

Its complement is ``excludes:``, which declares that two states cannot hold at the
same *instant* (``DRIVING excludes [PARKED]``). Together the two relations separate
a genuine simultaneous reading from a session that merely *sequenced* through both
— the latter needs a ``state_spans`` timeline for per-capture filtering to be
exact. See :func:`canair.states.contradictory_states`.

A bare ``canair states`` lists the vocabulary (like ``canair bus``); the edit
subcommands surgically modify ``vehicle_states.yaml`` (comment-preserving,
re-validated, reverted on failure).

The ``--help`` examples are :mod:`.epilog`. :mod:`.parser` declares the kinds, :mod:`.listing`
and :mod:`.show` print the vocabulary, and :mod:`.edit` changes it.

Belongs: `canair states`: the parser, the listing and the edits.
Not here: the vocabulary and its predicates (`states/`) and the YAML edits (`yaml_edit/`).
Imports: `states`, `ecus`, `yaml_edit` and below.
"""

from .parser import NAME, add_parser, run

__all__ = ["NAME", "add_parser", "run"]
