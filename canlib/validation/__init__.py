"""Pure profile validators: parsed data in, errors out (no printing, no exit).

``canair validate`` (:mod:`canlib.commands.validate`) is the CLI shell that reports
these, and the library editors call them directly as their post-write guard. Only
the signals domain lives here so far; the rest is planned in
``plans/2026-08-05-layering-and-module-size-followups.md`` (Part A).
"""
