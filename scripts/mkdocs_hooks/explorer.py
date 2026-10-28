"""MkDocs hook: generate the profile explorer's data files when the docs site is built.

For every bundled profile this writes ``assets/explorer/data/<profile>.json`` (the dict from
``canlib.explorer_export.export_profile``) plus an ``index.json`` listing them, as virtual
files, so the data always matches ``profiles/`` and is never committed. Decoded capture series
go alongside: ``data/<profile>/sessions.json`` and one ``data/<profile>/s/<id>.json`` per
session (``canlib.explorer_series``), fetched only when a session is opened. The page that renders
them is ``docs/profiles/explorer.md`` with ``docs/assets/explorer/explorer.{js,css}``.

Source links are pinned to the commit being built (``GITHUB_SHA``) so a line number cannot
drift when the YAML is edited later; a local build falls back to ``main``.

Plan: plans/2026-10-21-profile-explorer.md
"""

from __future__ import annotations

import json
import os
from pathlib import Path

from mkdocs.exceptions import PluginError
from mkdocs.structure.files import File, Files

REPO_ROOT = Path(__file__).resolve().parents[2]
PROFILES_DIR = REPO_ROOT / "profiles"
DATA_DIR = "assets/explorer/data"
BLOB_ROOT = "https://github.com/philipkocanda/canair/blob/"


def _blob_base() -> str:
    return f"{BLOB_ROOT}{os.environ.get('GITHUB_SHA') or 'main'}/"


def _dump(data: dict | list) -> str:
    return json.dumps(data, ensure_ascii=False, separators=(",", ":"), sort_keys=False)


def on_files(files: Files, config) -> Files:
    from canlib.explorer.export import export_profile
    from canlib.explorer.series import export_sessions, rx_by_address
    from canlib.profile import looks_like_profile

    roots = [p for p in sorted(PROFILES_DIR.iterdir()) if looks_like_profile(p)]
    index = []
    for root in roots:
        uri = f"{DATA_DIR}/{root.name}.json"
        if files.get_file_from_path(uri) is not None:
            raise PluginError(f"docs/{uri} is generated at build time by explorer.py; delete it.")
        data = export_profile(root, blob_base=_blob_base())
        sessions, documents = export_sessions(root, rx_by_address(data))
        data["profile"]["sessions"] = len(sessions)
        files.append(File.generated(config, uri, content=_dump(data)))
        if sessions:
            files.append(
                File.generated(
                    config, f"{DATA_DIR}/{root.name}/sessions.json", content=_dump(sessions)
                )
            )
            for session_id, doc in documents.items():
                files.append(
                    File.generated(
                        config, f"{DATA_DIR}/{root.name}/s/{session_id}.json", content=_dump(doc)
                    )
                )
        index.append(
            {
                "name": root.name,
                "car_model": data["profile"]["car_model"],
                "maturity": data["profile"]["maturity"],
                "stats": data["profile"]["stats"],
                "sessions": len(sessions),
            }
        )
    files.append(File.generated(config, f"{DATA_DIR}/index.json", content=_dump(index)))
    return files
