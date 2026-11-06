"""The DTC log file's format version and its writer.

A leaf of :mod:`canair.dtc.log`: the upgrade (:mod:`canair.dtc.log_upgrade`) writes the
log too, and the log runs the upgrade, so both import this instead of each other.
"""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path

from ruamel.yaml.comments import CommentedMap

from canair.core.yaml_rt import dump

FORMAT = 2
SECTIONS = ("scans", "clears")

HEADER = (
    "DTC scan history recorded by `canair dtc` for one profile's car. This is the only\n"
    "record of it: do not delete. View it with `canair dtc log`."
)


def write_log(data: Mapping, path: Path) -> Path:
    """Write a whole log document to ``path`` (creating its directory), with the header."""
    doc = data if isinstance(data, CommentedMap) else CommentedMap(data)
    if doc.get("format") != FORMAT:
        doc.insert(0, "format", FORMAT)
    if not doc.ca.comment:
        doc.yaml_set_start_comment(HEADER)
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w") as f:
        dump(doc, f)
    return path
