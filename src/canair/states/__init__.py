"""Vehicle operating-state definitions + auto-suggestion.

A *state* is a named, standardized description of the car's power/operating
condition (e.g. ``DEEPSLEEP``, ``ACC``, ``READY``, ``CHARGING``) recorded on a
capture session. Historically this field was free text; profiles can now declare
a canonical, ordered list of states in ``profiles/<name>/vehicle_states.yaml`` so the
vocabulary is consistent across vehicles and comparable between captures.

Each state may carry a ``when:`` predicate over decoded PID values, written as a
small boolean expression referencing parameters by ``ECU.PARAM``::

    states:
      - name: CHARGING
        description: Actively charging (implies plugged)
        when: "BMS.BATTERY_CURRENT < -1"
      - name: READY
        when: "VCU.CAR_READY == 1"
      - name: DEEPSLEEP
        when: "__no_response__"

``suggest_states`` evaluates the rules against the latest decoded values and
returns every match (a session is naturally composite, e.g. ``READY, PARKED``) —
implementing the project goal of using known PIDs to deduce vehicle state.

More than one state is genuinely true at once, so a state may declare which
broader states it is a *specialization* of::

    states:
      - name: DRIVING
        implies: [READY]
        when: "ESC.REAL_SPEED_KMH > 0.5"

``most_specific_states`` uses that hierarchy to reduce a composite match to the states nothing
else implies (a single-state display picks DRIVING over READY); ``suggest_state`` wraps it.

Predicates use a whitelisted-AST evaluator (no ``eval``): boolean/comparison operators,
``ECU.PARAM`` names, literals and the sentinels ``__no_response__`` / ``__responded__``.
Evaluation is three-valued (Kleene): a sub-expression on an unavailable parameter is
:data:`UNKNOWN`, not ``False``, so a rule is suggested only on positive evidence.

Belongs: the state tokens (``tokens``), ``when:`` predicates with Kleene logic (``predicates``),
the ``implies:`` and ``excludes:`` relations (``implies``), the loaded vocabulary
(``vocabulary``), suggestion (``suggest``) and ECU and ``--state`` selectors (``selectors``).
Not here: edits (``yaml_edit/``) and inference from captures (``captures/state_inference.py``).
Imports: ``expression`` and ``core``; ``refs`` also reads ``ecus``, ``variants`` and ``profile``.
"""
