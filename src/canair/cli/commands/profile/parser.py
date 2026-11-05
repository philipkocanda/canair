"""The ``canair profile`` parser."""

from __future__ import annotations

import argparse
from pathlib import Path

from canair.profile.scaffold import DEFAULT_INIT

from .listing import cmd_list
from .pick import cmd_default
from .scaffold import cmd_adopt, cmd_create, cmd_overlay
from .show import cmd_path, cmd_show
from .use import cmd_use

NAME = "profile"


ALIASES = ["prof"]


def add_parser(subparsers) -> argparse.ArgumentParser:
    parser = subparsers.add_parser(
        NAME,
        aliases=ALIASES,
        help="List, inspect, and create vehicle profiles",
        description="List, inspect, and create vehicle profiles — the per-vehicle\n"
        "bundles that hold all the reverse-engineering data. `show` lists every\n"
        "member of a bundle (definitions, captures, references, generated output).\n\n"
        "Subcommands:\n"
        "  list            list every discovered profile (bundled + user)\n"
        "  show [NAME]     details of a profile (ECU/PID counts, paths); default active\n"
        "  path [NAME]     print a profile's root directory (handy for scripting)\n"
        "  use NAME        set NAME as the default profile (default_profile in config)\n"
        "  create NAME     scaffold a new empty profile bundle\n"
        "  adopt NAME      copy a read-only profile to ~/.config/canair/profiles/ to write to it\n"
        "  overlay NAME    record your own captures over a read-only profile's definitions\n\n"
        "A bare `canair profile` opens an interactive arrow-key picker on a TTY (choose\n"
        "the default profile, then your car when it has several variants — re-select the\n"
        "current profile to change only the variant); piped it prints the list. Select the\n"
        "active profile with the global --profile flag, CANAIR_PROFILE, or\n"
        "default_profile in config (set the last with `canair profile use NAME`).",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""\
examples:
  canair profile                              # interactive picker (TTY) / list (piped)
  canair profile show                         # details of the active profile
  canair profile show hyundai-ioniq-ae-ev              # details of a named profile
  canair profile path                         # print the active profile's directory
  canair profile use hyundai-ioniq-ae-ev --variant 28kwh   # set the default profile + your car
  canair profile create kia-ev6-cv-ev --car-model "Kia EV6 (CV)"
  canair profile create kia-ev6-cv-ev --car-model "Kia EV6 (CV)" --set-default
  canair profile adopt hyundai-ioniq-ae-ev              # writable copy under ~/.config/canair
  canair profile overlay hyundai-ioniq-ae-ev            # keep its definitions, record your own captures
""",
    )
    sub = parser.add_subparsers(dest="profile_command")

    lst = sub.add_parser("list", help="List discovered profiles")
    lst.set_defaults(_profile_func=cmd_list)

    show = sub.add_parser("show", help="Show details of a profile (default: active)")
    show.add_argument("name", nargs="?", help="Profile name (default: active)")
    show.set_defaults(_profile_func=cmd_show)

    pth = sub.add_parser("path", help="Print the root directory of a profile")
    pth.add_argument("name", nargs="?", help="Profile name (default: active)")
    pth.set_defaults(_profile_func=cmd_path)

    use = sub.add_parser(
        "use",
        help="Set the default profile, and which of its variants your car is",
        description="Set NAME as the default profile (default_profile in the user config) "
        "and, with --variant, which of its variants your car is "
        "(profile_settings.NAME.variant, remembered per profile), and with --pin, which "
        "revision an ECU of it is. On a terminal, a profile "
        "with several variants and no --variant opens a picker. The name must be a "
        "discovered profile; an old, renamed name is accepted and saved as the current one.",
    )
    use.add_argument("name", help="Profile name to set as default")
    use.add_argument(
        "--variant",
        dest="use_variant",
        metavar="ID",
        help="The variant your car is (a variants.yaml ID), remembered for this profile",
    )
    use.add_argument(
        "--pin",
        dest="pins",
        action="append",
        metavar="ECU=ID",
        help="Which revision an ECU of your car is (one of its revisions: IDs), remembered "
        "for this profile (profile_settings.NAME.revisions). Repeat for several ECUs. For "
        "when canair cannot tell on its own; overridden for one command by --revision",
    )
    use.set_defaults(_profile_func=cmd_use)

    crt = sub.add_parser(
        "create",
        aliases=["init", "new"],
        help="Scaffold a new empty profile",
        description="Create a new vehicle profile bundle (ecus/, profile.yaml, captures/, out/).",
    )
    crt.add_argument("name", help="Profile name (used as the directory name)")
    crt.add_argument("--car-model", help="Vehicle description (e.g. 'VW e-Golf 2019')")
    crt.add_argument("--init", help=f"ELM327 init string (default: {DEFAULT_INIT})")
    crt.add_argument(
        "--path",
        type=Path,
        help="Create at this directory instead of ~/.config/canair/profiles/<name>",
    )
    crt.add_argument(
        "--set-default",
        action="store_true",
        help="Set this profile as default_profile in the user config",
    )
    crt.add_argument("--force", action="store_true", help="Allow a non-empty target directory")
    crt.set_defaults(_profile_func=cmd_create)

    adopt = sub.add_parser(
        "adopt",
        help="Copy a profile to ~/.config/canair/profiles/ so you can write to it",
        description="Copy a discovered profile into ~/.config/canair/profiles/<name>, "
        "where it shadows the original by name and is writable. Use it when the "
        "profile you are reading is read-only or lives in an install snapshot "
        "(site-packages), whose contents a reinstall replaces. Generated out/ is "
        "not copied — regenerate it with `canair wican autopid write`.",
        epilog="Working on a repo-bundled profile you intend to contribute back? "
        "Point canair at the checkout instead — `canair config set profiles_dir "
        "<clone>/profiles` — so your edits stay in git.",
    )
    adopt.add_argument("name", help="Name of the discovered profile to copy")
    adopt.add_argument(
        "--set-default",
        action="store_true",
        help="Set this profile as default_profile in the user config",
    )
    adopt.add_argument(
        "--force", action="store_true", help="Overwrite an existing user copy of this profile"
    )
    adopt.set_defaults(_profile_func=cmd_adopt)

    ovl = sub.add_parser(
        "overlay",
        help="Record into your own captures/ while definitions stay with the base profile",
        description="Create a capture layer over a discovered profile: a bundle at "
        "~/.config/canair/profiles/<name>/ holding an `extends:` marker and an empty "
        "captures/. Nothing is copied, so the base profile's definitions keep "
        "resolving (and keep tracking upstream) while every capture you record lands "
        "in your layer. Analysis reads both layers; the base layer's captures are "
        "read-only. Use `adopt` instead when you need to edit definitions too.",
    )
    ovl.add_argument("name", help="Name of the discovered profile to layer onto")
    ovl.add_argument(
        "--set-default",
        action="store_true",
        help="Set this profile as default_profile in the user config",
    )
    ovl.set_defaults(_profile_func=cmd_overlay)

    parser.set_defaults(func=run, _profile_func=cmd_default)
    return parser


def run(args) -> int:
    return args._profile_func(args)
