"""Live operations against the car: one coroutine per job, over a connected ``Terminal``.

Each ``mode_*`` coroutine takes a ``Terminal`` (never a concrete terminal) and typed options, runs
one operation (read a PID set, poll a monitor, sweep a range, read DTCs, actuate an output) and
reports through its renderer. ``session`` runs one under the shared error policy.

Belongs: device orchestration over the ``Terminal`` protocol, in one sub-package per family:
``read`` (the step executor, ``raw``), ``monitor`` (the controller, recorder, poller and picker
logic), ``scan`` (the sweeps), ``discover``, ``identity``, ``actuate`` (IOControl, routines and
profile actions), ``dtc``, ``memory`` (ReadMemoryByAddress reads) and ``elm`` (the ELM327-only
tools). ``session``, ``status`` and ``revision_probe`` serve all of them.
Not here: flags (``cli/commands/``), choosing a transport (``transport/``), what may be sent
(``safety/``) and the selector and step grammar (``query/``).
Imports: ``transport`` (the protocol only), ``safety``, ``captures``, ``ecus``, ``query``,
``analysis``, ``uds`` and below. It does not import ``cli``.

This package imports nothing eagerly, so a module such as ``monitor/schedule`` stays a cheap import
for the CLI parser.
"""
