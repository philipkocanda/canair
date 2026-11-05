"""The ``canair config`` parser."""

from __future__ import annotations

import argparse

from canair.core.paths import CONFIG_DOCS_URL

from .description import DESCRIPTION
from .edit import cmd_edit, cmd_example, cmd_get, cmd_path, cmd_set, cmd_unset
from .keys import SET_EPILOG, set_description
from .show import cmd_show

NAME = "config"


def add_parser(subparsers) -> argparse.ArgumentParser:
    parser = subparsers.add_parser(
        NAME,
        help="View and manage user configuration",
        description=DESCRIPTION,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    sub = parser.add_subparsers(dest="config_command")

    show = sub.add_parser("show", help="Show config locations and effective settings")
    show.add_argument("--json", action="store_true", help="Emit machine-readable JSON")
    show.set_defaults(_config_func=cmd_show)

    example = sub.add_parser(
        "example",
        help="Print the commented, fully-documented example config",
        description=(
            "Print `config.example.yaml` — the authoritative, fully-commented "
            "reference for every config key. See also: " + CONFIG_DOCS_URL
        ),
    )
    example.set_defaults(_config_func=cmd_example)

    pth = sub.add_parser("path", help="Print the user config file path")
    pth.set_defaults(_config_func=cmd_path)

    get = sub.add_parser("get", help="Print a single (dotted) config value")
    get.add_argument("key", help="Config key, e.g. default_wican or transport.port")
    get.set_defaults(_config_func=cmd_get)

    st = sub.add_parser(
        "set",
        help="Set a (dotted) config value",
        description=set_description(),
        epilog=SET_EPILOG,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    st.add_argument(
        "key",
        nargs="?",
        help="Config key, e.g. transport.type or devices.home.host",
    )
    st.add_argument(
        "value",
        nargs="?",
        help="Value (coerced to int/float/bool where unambiguous)",
    )
    st.add_argument(
        "-s",
        "--string",
        action="store_true",
        help="Store the value verbatim as a string (skip int/bool coercion)",
    )
    st.set_defaults(_config_func=cmd_set)

    un = sub.add_parser("unset", help="Remove a (dotted) config value")
    un.add_argument("key", help="Config key to remove")
    un.set_defaults(_config_func=cmd_unset)

    ed = sub.add_parser("edit", help="Open the user config in $EDITOR")
    ed.set_defaults(_config_func=cmd_edit)

    parser.set_defaults(func=run, _config_func=cmd_show)
    return parser


def run(args) -> int:
    return args._config_func(args)
