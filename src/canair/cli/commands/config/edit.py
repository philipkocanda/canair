"""``canair config`` kinds that read or change one key."""

from __future__ import annotations

from canair.core.paths import CONFIG_DOCS_URL, CONFIG_EXAMPLE_FILE
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
    from canair.config import user_config_file

    print(user_config_file())
    return 0


def cmd_example(args) -> int:
    if not CONFIG_EXAMPLE_FILE.exists():
        messages.error(f"{CONFIG_EXAMPLE_FILE} not found; see {CONFIG_DOCS_URL} instead")
        return 1
    print(CONFIG_EXAMPLE_FILE.read_text(), end="")
    return 0


def cmd_get(args) -> int:
    from canair.config import get_config_key

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
    from canair.config import coerce_scalar, get_config_key, load_config, set_config_key

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

    old = get_config_key(args.key)
    if old == value:
        print(f"{args.key} is already {value!r} (unchanged)")
        return 0

    path = set_config_key(args.key, value)
    change = f"{args.key} = {value!r}" if old is None else f"{args.key}: {old!r} → {value!r}"
    echo_edit(change, path)
    return 0


def cmd_unset(args) -> int:
    from canair.config import unset_config_key

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

    from canair.config import ensure_config_dir, user_config_file

    ensure_config_dir()
    path = user_config_file()
    editor = os.environ.get("EDITOR") or os.environ.get("VISUAL")
    if not editor:
        editor = next((e for e in ("nano", "vim", "vi") if shutil.which(e)), None)
    if not editor:
        messages.error(f"no editor found: set $EDITOR or edit {path} directly")
        return 1
    return subprocess.call([*editor.split(), str(path)])
