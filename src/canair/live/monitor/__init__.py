"""``canair monitor``: poll a set of PIDs on a schedule, show them live, optionally record them.

* :mod:`.controller` — the monitor's state and one poll tick (it composes the rest)
* :mod:`.run` — the entry point and the poll loops, interactive and piped
* :mod:`.poll_elm`, :mod:`.raw_run` and :mod:`.raw_poller` — the ELM327 poller, and the pipelined
  raw ``slcan-tcp`` path with its poller
* :mod:`.schedule`, :mod:`.backoff` and :mod:`.health` — which PIDs a tick reads, slowing a silent
  one, and whether the link answers at all
* :mod:`.record`, :mod:`.record_write`, :mod:`.history` and :mod:`.link_events` — recording, the
  on-screen history, gaps
* :mod:`.view` — the display model as plain values (labels, titles, view modes)
* :mod:`.render`, :mod:`.render_cache` and :mod:`.actuator_rows` — the only modules that build
  Rich text: the values, the per-PID block cache, and the actuator pane's rows
* :mod:`.stats` and :mod:`.state` — value ranges, state hints
* :mod:`.foreground` — the user's requests (a session switch, an actuation), run one at a time
  beside the poll and interleaved with it by the transport, request by request
* :mod:`.actuation` — the ``--actuate`` ECUs' actuators and routines, their sessions, and their
  release on exit and after a reconnect
* :mod:`.selectors`, :mod:`.select`, :mod:`.sessions` and :mod:`.edit` — changing what is polled
* :mod:`.reconnect` and :mod:`.options` — re-homing a dropped session, and the options as values

Belongs: everything between the CLI and the screen for one monitoring session, over a
``Terminal`` or a raw client. A module here may import its siblings.
Not here: the Textual screens (``tui/apps/monitor/``), the one-shot step executor
(``live/read/``), the flags (``cli/commands/monitor``) and the capture store (``captures/``).
Imports: ``transport``, ``safety``, ``captures``, ``ecus``, ``query``, ``analysis``, ``render``,
``states``, ``uds`` and below, ``live.read`` for the step primitives and ``live.actuate`` for
the actuation banks.

This package imports nothing eagerly. The CLI parser imports ``schedule`` at start-up, and a
package ``__init__`` that imported the controller would load the whole monitor with it.
"""
