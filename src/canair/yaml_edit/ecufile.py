"""Addressing one ECU's file: where it is, how to validate it, what to refresh after a write.

Both ECU-file engines (the ruamel editors in :mod:`canair.yaml_edit.ecu` and
:mod:`canair.yaml_edit.revisions`, and the text editors in :mod:`canair.yaml_edit.pids`) write
``ecus/<name>.yaml``. They share the file lookup (:mod:`canair.ecus.files`), the schema
check and the cache that must be dropped after a write, so a file edited by one engine is judged
exactly as one edited by the other.
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

from canair.core.edit_errors import EditError
from canair.ecus.files import find_by_name
from canair.ecus.pids import clear_cache as clear
from canair.profile.model import Profile
from canair.validation.pids import validate_pids_file


def clear_cache() -> None:
    """Drop the memoized ECU-definition load (stale after any write to ``ecus/``)."""

    clear()


def schema_validator(error: type[EditError]) -> Callable[[Path], None]:
    """A check that raises ``error`` when ``canair validate pids`` rejects the file.

    ``validate_pids_file`` derives the profile from the file's own path, so a write to a profile
    that is not the active one validates without a resolvable active profile.
    """

    def validate(path: Path) -> None:
        ok, msg = validate_pids_file(path)
        if not ok:
            raise error(f"ECU file invalid after edit (reverted):\n{msg}")

    return validate


def find_ecu_file(
    ecu_name: str, ecus_dir: Path | None = None, *, error: type[EditError], profile: Profile
) -> Path:
    """The ``ecus/<ecu>.yaml`` file that defines ``ecu_name`` (``error`` if none does).

    The write-target resolver for every name-keyed editor. An edit must never invent a
    definition file: that is ``canair ecu add``'s job.
    """

    fpath = find_by_name(ecu_name, ecus_dir, profile=profile)
    if fpath is None:
        raise error(f"ECU {ecu_name!r} not found in any ecus/*.yaml")
    return fpath
