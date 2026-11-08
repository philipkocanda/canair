"""The operations that change the car's state: IOControl, RoutineControl and profile actions.

* :mod:`.iocontrol` — ``canair io``, the state of the actuator view (:mod:`.iocontrol_render`
  holds its rows; the Textual screen is ``tui/apps/iocontrol/``)
* :mod:`.iocontrol_actuate` and :mod:`.routine_bank` — one ECU's IOControl actuators and
  routines, their state and their requests, shared by ``io``, ``routines``, the ``read``
  pipeline and the monitor's actuator pane
* :mod:`.ports` — what a bank needs from its owner: a session (:class:`~.ports.OwnSessions`
  for a one-ECU view, :class:`~.ports.SharedSessions` for a manager somebody else keeps alive)
  and a place to report
* :mod:`.iocontrol_execute` and :mod:`.iocontrol_list` — one actuation, and the profile listing
* :mod:`.routines` — ``canair routines``, the state and the rows of the routine view (the
  Textual screen is ``tui/apps/routines/``)
* :mod:`.routines_execute` and :mod:`.routines_list` — one routine run (and the refusal of a
  KWP2000 ECU, which reads UDS RoutineControl as a routine start), and the profile listing
* :mod:`.action` — ``canair action run``
* :mod:`.not_found` — the error for an ECU, DID or RID the profile does not define

Belongs: actuating and releasing outputs and running routines over a ``Terminal``. Everything here
is mutative and releases its actuators when the session ends.
Not here: sweeping for actuators and routines (``live/scan/``), what may be sent (``safety/``),
the action model and its guards (``actions/``) and the flags (``cli/commands/``).
Imports: ``transport`` (the protocol only), ``safety``, ``actions``, ``ecus``, ``captures``,
``render``, ``uds`` and below.

This package imports nothing eagerly, like ``live``.
"""
