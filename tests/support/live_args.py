"""A full live-command namespace, for tests that call a handler or the runtime directly.

A real command's parser exposes only the flags it means; a test that builds the namespace by hand
starts from every flag any live command has, at its default.
"""

from __future__ import annotations

import argparse

from canlib.live.poll_schedule import DEFAULT_SCHEDULE

LIVE_ARGS_DEFAULTS: dict = {
    # the operand each handler reads
    "param": None,
    "ecu": None,
    "raw": None,
    "scan": False,
    "skm_wakeup": False,
    "identity": False,
    "discover": False,
    "dtc": None,
    "dtc_all": False,
    "iocontrol": None,
    "routines": None,
    "routines_scan": None,
    "iocontrol_scan": None,
    "sessions_scan": None,
    "multi": None,
    "action": None,
    # flags
    "pid": None,
    "did": None,
    "off": False,
    "rid": None,
    "sf": "results",
    "tx": None,
    "service": "21",
    "range": "01-FF",
    "append": None,
    "session": False,
    "hold": False,
    "wake": False,
    "repl": False,
    "protocol": "auto",
    "monitor": None,
    "schedule": DEFAULT_SCHEDULE,
    "no_backoff": False,
    "keep_changes": False,
    "keep_all": False,
    "keep": None,
    "save": False,
    "label": None,
    "state": None,
    "notes": None,
    "rulers": False,
    "rid_range": "F000-F0FF",
    "did_range": None,
    "throttle_ms": 150,
    "level": "acc",
    "target": None,
    "interval": 1.0,
    "delay": 0.2,
    # `None`, not a resolved device address: every live subcommand exposes its
    # own `--wican` (default `None`, see add_connection_args below) via
    # add_connection_args, so this entry is only a fallback for callers (tests)
    # that build an argparse.Namespace straight from CANAIR_DEFAULTS. It must
    # match that real default and must NOT eagerly resolve a config-backed
    # value here — this dict is built at import time, and a config-derived
    # default previously forced every import of this module to read the user's
    # ~/.config/canair/config.yaml (a live liveness-lookup done purely because
    # the module was imported, not because a device was ever contacted).
    "wican": None,
    "no_fallback": False,
    "no_learn_frames": False,
    "wait": False,
    "timeout": 3.0,  # WebSocket response timeout (s); fixed default, no CLI flag
    "elm_timeout": None,
    "json": False,
    "verbose": False,
    "timings": False,
    "reboot": False,
    "unsafe": False,
    "force": False,
}


def live_args(**overrides) -> argparse.Namespace:
    """The namespace of a live command at its defaults, with ``overrides`` applied."""
    return argparse.Namespace(**{**LIVE_ARGS_DEFAULTS, **overrides})
