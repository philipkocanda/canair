"""``canair config`` kinds that read or change one key."""

from __future__ import annotations

import sys

from canair.config.file import (
    ALLOW_RESET_KEY,
    ALLOW_UNSAFE_KEY,
    coerce_scalar,
    ensure_config_dir,
    get_config_key,
    load_config,
    reset_allowed,
    set_config_key,
    unsafe_allowed,
    unset_config_key,
    user_config_file,
)
from canair.core.paths import CONFIG_DOCS_URL, CONFIG_EXAMPLE_FILE
from canair.core.prompt import typed_confirm
from canair.render import messages
from canair.yaml_edit.echo import echo_edit

from .keys import (
    LIST_KEYS,
    invalid_value,
    known_key,
    missing_key_help,
    missing_value_help,
)


def cmd_path(args) -> int:
    print(user_config_file())
    return 0


def cmd_example(args) -> int:
    if not CONFIG_EXAMPLE_FILE.exists():
        messages.error(f"{CONFIG_EXAMPLE_FILE} not found; see {CONFIG_DOCS_URL} instead")
        return 1
    print(CONFIG_EXAMPLE_FILE.read_text(), end="")
    return 0


def cmd_get(args) -> int:
    value = get_config_key(args.key)
    if value is None:
        messages.error(f"{args.key} is not set")
        return 1
    if isinstance(value, (dict, list)):
        import json

        print(json.dumps(value, indent=2, default=str))
    else:
        print(value)
    return 0


def cmd_set(args) -> int:
    if args.key is None:
        missing_key_help()
        return 2
    if args.value is None:
        missing_value_help(args.key)
        return 2

    if args.key in LIST_KEYS and not args.string:
        value: object = [p.strip() for p in args.value.split(",") if p.strip()]
    else:
        value = args.value if args.string else coerce_scalar(args.value)

    err = invalid_value(args.key, value)
    if err:
        messages.error(err)
        return 2

    if not known_key(args.key):
        messages.note(
            f"'{args.key}' is not a recognized config key "
            "(see `canair config set --help`); setting it anyway"
        )

    # wican_addresses is the legacy device map, superseded by `devices:`.
    if args.key.startswith("wican_addresses."):
        alias = args.key.split(".", 1)[1]
        messages.warning(
            f"'wican_addresses' is deprecated — prefer "
            f"`canair config set devices.{alias}.host {args.value}` "
            "(devices support per-device transport/port/bitrate)"
        )
        if isinstance(load_config().get("devices"), dict) and load_config()["devices"]:
            messages.warning(
                "a `devices:` block is defined, so `wican_addresses` is "
                "ignored at runtime — this value will have no effect"
            )

    # The merged config also reads the legacy repo-local file, which the safety gates ignore.
    enabling_gate = value is True and args.key in _GATES and not _GATES[args.key][0]()
    old = get_config_key(args.key)
    if old == value and not enabling_gate:
        print(f"{args.key} is already {value!r} (unchanged)")
        return 0

    if enabling_gate:
        refused = _consent_to_enable(args.key)
        if refused is not None:
            return refused

    path = set_config_key(args.key, value)
    change = f"{args.key} = {value!r}" if old is None else f"{args.key}: {old!r} → {value!r}"
    echo_edit(change, path)
    return 0


# Each safety gate: whether it is on now, and what the consent prompt says it enables.
_GATES = {
    ALLOW_UNSAFE_KEY: (
        unsafe_allowed,
        f"{ALLOW_UNSAFE_KEY} lets live commands take --unsafe. Then a request the blocklist "
        "refuses is sent after you type YES,\n!! and such a request can damage an ECU beyond "
        "repair. Enable --unsafe?",
    ),
    ALLOW_RESET_KEY: (
        reset_allowed,
        f"{ALLOW_RESET_KEY} lets `canair reset` restart an ECU. A restarting ECU stops "
        "working, which is dangerous\n!! while the car is in use. Enable `canair reset`?",
    ),
}


def _consent_to_enable(key: str) -> int | None:
    """Ask the user to type YES before a safety gate is enabled; the exit code if not.

    No flag answers this prompt, on purpose: the gate exists so that a flag passed with good
    intentions (by an agent, or a script) cannot reach what it guards. A run with no terminal
    is refused (exit 2) and a decline exits 1.
    """
    if not sys.stdin.isatty():
        messages.error(
            f"{key} can only be enabled at an interactive terminal, and no flag skips this"
        )
        return 2
    return None if typed_confirm(_GATES[key][1], "YES") else 1


def cmd_unset(args) -> int:
    path, removed = unset_config_key(args.key)
    if not removed:
        messages.error(f"{args.key} was not set")
        return 1
    echo_edit(f"removed {args.key}", path)
    return 0


def cmd_edit(args) -> int:
    import os
    import shutil
    import subprocess

    ensure_config_dir()
    path = user_config_file()
    editor = os.environ.get("EDITOR") or os.environ.get("VISUAL")
    if not editor:
        editor = next((e for e in ("nano", "vim", "vi") if shutil.which(e)), None)
    if not editor:
        messages.error(f"no editor found: set $EDITOR or edit {path} directly")
        return 1
    return subprocess.call([*editor.split(), str(path)])
