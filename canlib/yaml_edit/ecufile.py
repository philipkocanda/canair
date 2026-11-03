"""Addressing one ECU's file: where it is, how to validate it, what to refresh after a write.

Both ECU-file engines (the ruamel editors in :mod:`canlib.yaml_edit.ecu` and
:mod:`canlib.yaml_edit.revisions`, and the text editors in :mod:`canlib.yaml_edit.pids`) write
``ecus/<name>.yaml``. They share the file lookup (:mod:`canlib.ecus.files`), the schema
check and the cache that must be dropped after a write, so a file edited by one engine is judged
exactly as one edited by the other.
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

from .errors import EditError


def clear_cache() -> None:
    """Drop the memoized ECU-definition load (stale after any write to ``ecus/``)."""
    from canlib.pids import clear_cache as clear

    clear()


def schema_validator(error: type[EditError]) -> Callable[[Path], None]:
    """A check that raises ``error`` when ``canair validate pids`` rejects the file.

    ``validate_pids_file`` derives the profile from the file's own path, so a write to a profile
    that is not the active one validates without a resolvable active profile.
    """

    def validate(path: Path) -> None:
        from canlib.commands.validate import validate_pids_file

        ok, msg = validate_pids_file(path)
        if not ok:
            raise error(f"ECU file invalid after edit (reverted):\n{msg}")

    return validate


def find_ecu_file(ecu_name: str, ecus_dir: Path | None = None, *, error: type[EditError]) -> Path:
    """The ``ecus/<ecu>.yaml`` file that defines ``ecu_name`` (``error`` if none does).

    The write-target resolver for every name-keyed editor. An edit must never invent a
    definition file: that is ``canair ecu add``'s job.
    """
    from canlib.ecus.files import find_by_name

    fpath = find_by_name(ecu_name, ecus_dir)
    if fpath is None:
        raise error(f"ECU {ecu_name!r} not found in any ecus/*.yaml")
    return fpath
