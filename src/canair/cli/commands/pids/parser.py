"""The ``canair pids`` parser: the command, and the groups that declare its kinds."""

from __future__ import annotations

import argparse

from canair.cli.profile_context import target_profile
from canair.yaml_edit.memory import memory_pid_key

from . import ecu_facts, entries, migrate, pid_settings, research
from .epilog import EPILOG

NAME = "pids"


def add_parser(subparsers) -> argparse.ArgumentParser:
    parser = subparsers.add_parser(
        NAME,
        help="Author diagnostic PID params/research in ecus/ (domain A; see `signals` for domain B)",
        description="Safely edit ecus/ parameters and research entries (domain A — "
        "diagnostic UDS PIDs, freeform WiCAN expressions). The broadcast-frame "
        "(domain B) authoring counterpart is `canair signals` (linear signals/ maps).",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=EPILOG,
    )
    sub = parser.add_subparsers(dest="pids_command", required=True)
    entries.add_parsers(sub)
    research.add_parsers(sub)
    pid_settings.add_parsers(sub)
    migrate.add_parsers(sub)
    ecu_facts.add_parsers(sub)

    parser.set_defaults(func=run)
    return parser


def run(args) -> int:
    if isinstance(getattr(args, "pid", None), str):  # a 23/ADDRESS+SIZE token names its request
        args.pid = memory_pid_key(
            args.ecu, args.pid, getattr(args, "dir", None), profile=target_profile(args)
        )
    return args._pids_func(args)
