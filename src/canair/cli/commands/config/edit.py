"""``canair config`` kinds that read or change one key."""

from __future__ import annotations

import sys

from canair.config.file import (
    ALLOW_UNSAFE_KEY,
    coerce_scalar,
    ensure_config_dir,
    get_config_key,
    load_config,
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

    # The merged config also reads the legacy repo-local file, which unsafe_allowed() ignores.
    enabling_unsafe = args.key == ALLOW_UNSAFE_KEY and value is True and not unsafe_allowed()
    old = get_config_key(args.key)
    if old == value and not enabling_unsafe:
        print(f"{args.key} is already {value!r} (unchanged)")
        return 0

    if enabling_unsafe:
        refused = _consent_to_allow_unsafe()
        if refused is not None:
            return refused

    path = set_config_key(args.key, value)
    change = f"{args.key} = {value!r}" if old is None else f"{args.key}: {old!r} → {value!r}"
    echo_edit(change, path)
    return 0


def _consent_to_allow_unsafe() -> int | None:
    """Ask the user to type YES before ``--unsafe`` is enabled; the exit code if not.

    No flag answers this prompt, on purpose: the key exists so that a flag passed with good
    intentions (by an agent, or a script) cannot reach the ``--unsafe`` prompts. A run with no
    terminal is refused (exit 2) and a decline exits 1.
    """
    if not sys.stdin.isatty():
        messages.error(
            f"{ALLOW_UNSAFE_KEY} can only be enabled at an interactive terminal, "
            "and no flag skips this"
        )
        return 2
    question = (
        f"{ALLOW_UNSAFE_KEY} lets live commands take --unsafe. Then a request the blocklist "
        "refuses is sent after you type YES,\n!! and such a request can damage an ECU beyond "
        "repair. Enable --unsafe?"
    )
    return None if typed_confirm(question, "YES") else 1


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
