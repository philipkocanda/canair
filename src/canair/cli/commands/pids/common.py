"""What several groups of ``canair pids`` share."""

from __future__ import annotations

import argparse
from pathlib import Path

from canair.cli.commands._edit import clear_sentinel, edit_scope
from canair.cli.profile_context import target_profile
from canair.profile import active
from canair.yaml_edit.pids import (
    find_ecu_file,
)


def apply_in_scope(args: argparse.Namespace, do, ecu: str | None = None) -> Path:
    """Run ``do`` (the editor calls) in an edit scope; return the ECU's file for the echo."""
    with edit_scope(args):
        do()
    return find_ecu_file(ecu or args.ecu, pids_dir=args.dir, profile=target_profile(args))


def words_verb(text: str | None) -> str:
    """The confirmation for a free-text field: ``cleared`` or ``set (N words)``."""
    words = (text or "").split()
    return f"set ({len(words)} words)" if words else "cleared"


def ecu_names(ecus_dir: Path | None) -> list[str]:
    """Every ECU key defined under ``ecus_dir`` (default: the active profile)."""
    import yaml

    from canair.ecus.files import iter_ecu_files

    names: list[str] = []
    for path in iter_ecu_files(ecus_dir, profile=None if ecus_dir else active()):
        doc = yaml.safe_load(path.read_text())
        if isinstance(doc, dict):
            names.extend(str(k) for k in doc)
    return names


def int_or_clear(text: str) -> int | None:
    """``period``/``response_frames`` value: an integer, or the word ``clear`` for none."""
    return None if clear_sentinel(text) is None else int(text)
