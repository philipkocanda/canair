"""The operations that change the car's state: IOControl, RoutineControl, actions and resets.

* :mod:`.iocontrol` — ``canair io``, the state of the actuator view (:mod:`.iocontrol_actuate`
  and :mod:`.iocontrol_render` hold its actuation and its rows; the Textual screen is
  ``tui/apps/iocontrol/``)
* :mod:`.iocontrol_execute` and :mod:`.iocontrol_list` — one actuation, and the profile listing
* :mod:`.routines` — ``canair routines``, the state and the rows of the routine view (the
  Textual screen is ``tui/apps/routines/``)
* :mod:`.routines_execute` and :mod:`.routines_list` — one routine run (and the refusal of a
  KWP2000 ECU, which reads UDS RoutineControl as a routine start), and the profile listing
* :mod:`.action` — ``canair action run``
* :mod:`.ecu_reset` — ``canair reset``: one ECUReset, and the wait for the ECU to answer again
* :mod:`.not_found` — the error for an ECU, DID or RID the profile does not define

Belongs: actuating and releasing outputs, running routines and resetting an ECU over a
``Terminal``. Everything here is mutative and releases its actuators when the session ends.
Not here: sweeping for actuators and routines (``live/scan/``), what may be sent (``safety/``),
the action model and its guards (``actions/``) and the flags (``cli/commands/``).
Imports: ``transport`` (the protocol only), ``safety``, ``actions``, ``ecus``, ``captures``,
``render``, ``uds`` and below.

This package imports nothing eagerly, like ``live``.
"""
