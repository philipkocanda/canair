"""``canair ecu ECU edit``: open the ECU's file in the editor."""

from __future__ import annotations

import sys
from collections.abc import Mapping
from typing import Any

from canair.render import messages


def cmd_edit(info: Mapping[str, Any], tx_id: int) -> int:
    """Open the ECU's ``ecus/<name>.yaml`` file in ``$EDITOR`` (TTY only).

    A human escape hatch for bulk/awkward edits the surgical `canair pids`
    subcommands don't reach. It refuses to run when stdin/stdout isn't a
    terminal so agents can't drive it — they must use the validated `canair
    pids` editors instead. After the editor exits, the file is re-validated and
    any errors are surfaced (the edit is *not* auto-reverted — the user owns it).
    """
    import os
    import shutil
    import subprocess

    from canair.yaml_edit.ecu import find_ecu_file_by_tx

    name = info.get("name") or f"0x{tx_id:03X}"

    if not (sys.stdin.isatty() and sys.stdout.isatty()):
        messages.error(f"`canair ecu {name} edit` requires an interactive terminal")
        messages.note("it opens $EDITOR by design, so it can't be scripted or driven by an agent")
        messages.hint(
            "use the surgical, validated editors instead: `canair pids upsert-param`, "
            "`canair pids set-can-bus`, `canair ecu add`"
        )
        return 2

    path = find_ecu_file_by_tx(tx_id)
    if path is None or not path.exists():
        messages.error(f"no ecus/ file found for {name} (0x{tx_id:03X})")
        messages.hint("register it first with `canair ecu add`")
        return 1

    editor = os.environ.get("EDITOR") or os.environ.get("VISUAL")
    if not editor:
        editor = next((e for e in ("nano", "vim", "vi") if shutil.which(e)), None)
    if not editor:
        messages.error(f"no editor found: set $EDITOR or edit {path} directly")
        return 1

    rc = subprocess.call([*editor.split(), str(path)])
    if rc != 0:
        messages.warning(f"editor exited with status {rc}; skipping validation")
        return rc

    # Re-validate the edited file (not auto-reverted — the user owns the edit).
    from canair.cli.commands.validate.pids import run_pids

    messages.note(f"validating {path.name} …")
    vrc = run_pids([str(path)], stats=False)
    if vrc:
        messages.warning(
            f"validation failed: re-run `canair ecu {name} edit` to fix, or `canair validate pids`"
        )
    return vrc
