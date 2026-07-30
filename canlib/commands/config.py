"""``canair config`` — view and manage user configuration.

Shows the effective (merged) configuration, where it lives on disk, the
resolved transport, and the WiCAN address book — and edits the user config
(``$XDG_CONFIG_HOME/canair/config.yaml``) in place, preserving comments.

Subcommands:
  show            Show config file locations + effective settings (default)
  path            Print the user config file path (handy for scripting)
  get KEY         Print one value; KEY may be dotted (e.g. transport.port)
  set KEY VALUE   Set a value (dotted keys create nested mappings)
  unset KEY       Remove a value
  edit            Open the user config in $EDITOR

Examples:
  canair config
  canair config set default_wican home
  canair config set wican_addresses.home 10.0.2.86
  canair config set transport.type slcan-tcp
  canair config get transport.host
  canair config unset transport.port
"""

from __future__ import annotations

import argparse
import sys

NAME = "config"

# Keys accepted by `config set`, offered for tab-completion / help. Dotted keys
# with a wildcard leaf (wican_addresses.<alias>) are documented, not enumerated.
_KNOWN_KEYS = (
    "default_profile",
    "profiles_dir",
    "default_wican",
    "wican_addresses.<alias>",
    "wican_model",
    "check_for_updates",
    "grid_region",
    "transport.type",
    "transport.host",
    "transport.port",
    "transport.bitrate",
)


# Known keys whose value is an enum — validated up front so a typo gets a clear
# "valid: …" error instead of silently writing a value nothing will accept.
def _transport_types() -> tuple[str, ...]:
    from canlib.transport.config import VALID_TRANSPORTS

    return VALID_TRANSPORTS


def _grid_regions() -> tuple[str, ...]:
    from canlib.grid_regions import GRID_REGIONS

    return GRID_REGIONS


_WICAN_MODELS = ("pro", "classic")


def _keys_block() -> str:
    """Aligned 'known keys' list with valid values for enum keys."""
    width = max(len(k) for k in _KNOWN_KEYS)
    lines = []
    for k in _KNOWN_KEYS:
        vals = _enum_values(k)
        suffix = f"  valid: {', '.join(vals)}" if vals else ""
        lines.append(f"  {k:<{width}}{suffix}".rstrip())
    return "\n".join(lines)


def _set_description() -> str:
    return (
        "Set a config value:  canair config set KEY VALUE\n"
        "Dotted keys create nested mappings (e.g. transport.type).\n\n"
        "known keys:\n" + _keys_block()
    )


_SET_EPILOG = """\
examples:
  canair config set default_wican home
  canair config set wican_addresses.home 10.0.2.86
  canair config set transport.type slcan-tcp      # or: wican-ws
  canair config set wican_model pro               # or: classic
  canair config set check_for_updates false

Values are coerced to int/bool/null where unambiguous; pass --string to force a
string (e.g. a zero-padded id). Dotted keys create nested mappings.
"""


def add_parser(subparsers) -> argparse.ArgumentParser:
    parser = subparsers.add_parser(
        NAME,
        help="View and manage user configuration",
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    sub = parser.add_subparsers(dest="config_command")

    show = sub.add_parser("show", help="Show config locations and effective settings")
    show.add_argument("--json", action="store_true", help="Emit machine-readable JSON")
    show.set_defaults(_config_func=_cmd_show)

    pth = sub.add_parser("path", help="Print the user config file path")
    pth.set_defaults(_config_func=_cmd_path)

    get = sub.add_parser("get", help="Print a single (dotted) config value")
    get.add_argument("key", help="Config key, e.g. default_wican or transport.port")
    get.set_defaults(_config_func=_cmd_get)

    st = sub.add_parser(
        "set",
        help="Set a (dotted) config value",
        description=_set_description(),
        epilog=_SET_EPILOG,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    st.add_argument(
        "key",
        nargs="?",
        help="Config key, e.g. transport.type or wican_addresses.home",
    )
    st.add_argument(
        "value",
        nargs="?",
        help="Value (coerced to int/bool where unambiguous)",
    )
    st.add_argument(
        "-s",
        "--string",
        action="store_true",
        help="Store the value verbatim as a string (skip int/bool coercion)",
    )
    st.set_defaults(_config_func=_cmd_set)

    un = sub.add_parser("unset", help="Remove a (dotted) config value")
    un.add_argument("key", help="Config key to remove")
    un.set_defaults(_config_func=_cmd_unset)

    ed = sub.add_parser("edit", help="Open the user config in $EDITOR")
    ed.set_defaults(_config_func=_cmd_edit)

    parser.set_defaults(func=run, _config_func=_cmd_show)
    return parser


def _cmd_path(args) -> int:
    from canlib.config import user_config_file

    print(user_config_file())
    return 0


def _cmd_get(args) -> int:
    from canlib.config import get_config_key

    value = get_config_key(args.key)
    if value is None:
        print(f"{args.key} is not set", file=sys.stderr)
        return 1
    if isinstance(value, (dict, list)):
        import json

        print(json.dumps(value, indent=2, default=str))
    else:
        print(value)
    return 0


def _known_key(key: str) -> bool:
    """True if ``key`` is a recognized config key (exact or a known namespace)."""
    if key in _KNOWN_KEYS:
        return True
    prefix = key.split(".", 1)[0]
    namespaces = {k.split(".", 1)[0] for k in _KNOWN_KEYS if "." in k}
    return prefix in namespaces


def _enum_values(key: str) -> tuple[str, ...] | None:
    """Return the allowed values for a known enum key, else None."""
    if key == "transport.type":
        return _transport_types()
    if key == "wican_model":
        return _WICAN_MODELS
    if key == "grid_region":
        return _grid_regions()
    return None


def _invalid_value(key: str, value) -> str | None:
    """Return an error message if ``value`` is invalid for a known enum key."""
    if key == "transport.type" and value not in _transport_types():
        return f"invalid transport.type {value!r}; valid: {', '.join(_transport_types())}"
    if key == "wican_model" and str(value).strip().lower() not in _WICAN_MODELS:
        return f"invalid wican_model {value!r}; valid: {', '.join(_WICAN_MODELS)}"
    if key == "grid_region" and str(value).strip().upper() not in _grid_regions():
        return f"invalid grid_region {value!r}; valid: {', '.join(_grid_regions())}"
    return None


def _missing_key_help() -> None:
    """Print guidance when `config set` is called with no key."""
    print("error: missing KEY (and VALUE) to set.\n", file=sys.stderr)
    print("usage: canair config set KEY VALUE\n", file=sys.stderr)
    print("known keys:", file=sys.stderr)
    print(_keys_block(), file=sys.stderr)
    print("\nexamples:", file=sys.stderr)
    print("  canair config set default_wican home", file=sys.stderr)
    print("  canair config set transport.type slcan-tcp", file=sys.stderr)


def _missing_value_help(key: str) -> None:
    """Print key-aware guidance when `config set KEY` is missing its value."""
    print(f"error: missing VALUE for '{key}'.\n", file=sys.stderr)
    print(f"usage: canair config set {key} VALUE", file=sys.stderr)

    vals = _enum_values(key)
    if vals:
        print(f"\nvalid values: {', '.join(vals)}", file=sys.stderr)
        print(f"\nexample:\n  canair config set {key} {vals[0]}", file=sys.stderr)
        return

    if not _known_key(key):
        print(
            f"\nnote: '{key}' is not a recognized config key (see `canair config set --help`).",
            file=sys.stderr,
        )
    if key.startswith("wican_addresses."):
        print("\nexample:\n  canair config set wican_addresses.home 10.0.2.86", file=sys.stderr)
    else:
        print(f"\nexample:\n  canair config set {key} <value>", file=sys.stderr)


def _cmd_set(args) -> int:
    from canlib.config import coerce_scalar, get_config_key, set_config_key

    if args.key is None:
        _missing_key_help()
        return 2
    if args.value is None:
        _missing_value_help(args.key)
        return 2

    value = args.value if args.string else coerce_scalar(args.value)

    err = _invalid_value(args.key, value)
    if err:
        print(f"error: {err}", file=sys.stderr)
        return 2

    if not _known_key(args.key):
        print(
            f"note: '{args.key}' is not a recognized config key "
            "(see `canair config set --help`); setting it anyway.",
            file=sys.stderr,
        )

    old = get_config_key(args.key)
    if old == value:
        print(f"{args.key} is already {value!r} (unchanged)")
        return 0

    path = set_config_key(args.key, value)
    if old is None:
        print(f"{args.key} = {value!r}")
    else:
        print(f"{args.key}: {old!r} -> {value!r}")
    print(f"Saved to {path}")
    return 0


def _cmd_unset(args) -> int:
    from canlib.config import unset_config_key

    path, removed = unset_config_key(args.key)
    if not removed:
        print(f"{args.key} was not set", file=sys.stderr)
        return 1
    print(f"Removed {args.key} from {path}")
    return 0


def _cmd_edit(args) -> int:
    import os
    import shutil
    import subprocess

    from canlib.config import ensure_config_dir, user_config_file

    ensure_config_dir()
    path = user_config_file()
    editor = os.environ.get("EDITOR") or os.environ.get("VISUAL")
    if not editor:
        editor = next((e for e in ("nano", "vim", "vi") if shutil.which(e)), None)
    if not editor:
        print("No editor found. Set $EDITOR or edit directly:", file=sys.stderr)
        print(f"  {path}", file=sys.stderr)
        return 1
    return subprocess.call([*editor.split(), str(path)])


def _gather(args) -> dict:
    """Collect config locations + effective settings into a plain dict."""
    from canlib.config import (
        config_dir,
        load_config,
        user_config_file,
        user_profiles_dir,
        wican_model,
        wican_settings,
    )
    from canlib.constants import CONFIG_FILE

    cfg = load_config()
    user_file = user_config_file()

    info: dict = {
        "files": {
            "user": {"path": str(user_file), "exists": user_file.exists()},
            "legacy": {"path": str(CONFIG_FILE), "exists": CONFIG_FILE.exists()},
            "config_dir": str(config_dir()),
            "profiles_dir": str(user_profiles_dir()),
        },
        "config": cfg,
    }

    addresses, default_alias = wican_settings()
    info["wican"] = {
        "addresses": addresses,
        "default": default_alias,
        "model": wican_model(),
    }

    try:
        from canlib.transport import resolve_transport

        t = resolve_transport()
        info["transport"] = {
            "type": t.type,
            "host": t.host,
            "port": t.port,
            "bitrate": t.bitrate,
        }
    except Exception as e:
        info["transport"] = None
        info["transport_error"] = str(e)

    try:
        from canlib.profile import active

        prof = active()
        info["profile"] = {"name": prof.name, "root": str(prof.root)}
    except Exception as e:
        info["profile"] = None
        info["profile_error"] = str(e)

    return info


# Keys rendered on their own in the "Settings" block are shown from the
# effective config; these are surfaced elsewhere so we skip them there.
_SPECIAL_KEYS = {"wican_addresses", "transport", "wican_model"}


def _render(info: dict) -> None:
    from rich.console import Console

    c = Console()

    files = info["files"]
    c.print("\n  [bold]Config files[/bold]")
    for label, entry in (("user", files["user"]), ("legacy", files["legacy"])):
        mark = "[green]ok[/green]" if entry["exists"] else "[dim]not present[/dim]"
        c.print(f"    {label:<9}{entry['path']}  ({mark})")
    c.print(f"    {'profiles':<9}{files['profiles_dir']}")

    t = info.get("transport")
    if t:
        loc = t.get("host") or "?"
        if t.get("port"):
            loc = f"{loc}:{t['port']}"
        c.print(f"\n  [bold]Transport[/bold]   {t['type']}  [dim]({loc})[/dim]")
        if t.get("bitrate"):
            c.print(f"    {'bitrate':<9}{t['bitrate']}")
        c.print("    [dim]resolved from config; override with --transport/--wican[/dim]")
    elif info.get("transport_error"):
        c.print(f"\n  [bold]Transport[/bold]   [red]{info['transport_error']}[/red]")

    w = info["wican"]
    model = w.get("model", "pro")
    model_note = "" if model == "pro" else "  [dim](AutoPID sync disabled)[/dim]"
    c.print(f"\n  [bold]WiCAN[/bold]   model: {model}{model_note}")
    c.print("  [bold]WiCAN addresses[/bold]")
    for alias, addr in w["addresses"].items():
        default = "  [green](default)[/green]" if alias == w["default"] else ""
        c.print(f"    {alias:<9}{addr}{default}")

    cfg = info["config"]
    others = {k: v for k, v in cfg.items() if k not in _SPECIAL_KEYS}
    if others:
        c.print("\n  [bold]Settings[/bold]")
        width = max(len(k) for k in others)
        for k, v in others.items():
            c.print(f"    {k:<{width + 2}}{v}")

    p = info.get("profile")
    if p:
        c.print(f"\n  [bold]Active profile[/bold]  {p['name']}  [dim]{p['root']}[/dim]")
    elif info.get("profile_error"):
        c.print(f"\n  [bold]Active profile[/bold]  [yellow]{info['profile_error']}[/yellow]")
    c.print()


def _cmd_show(args) -> int:
    info = _gather(args)
    if getattr(args, "json", False):
        import json

        print(json.dumps(info, indent=2, default=str))
    else:
        _render(info)
    return 0


def run(args) -> int:
    return args._config_func(args)
