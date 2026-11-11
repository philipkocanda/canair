"""The data files that the docs site publishes next to its pages, as ``{site path: text}``.

Two sets, both derived from the repository so they always match it and are never committed:

- The profile explorer's data. For every bundled profile this holds
  ``assets/explorer/data/<profile>.json`` (the dict from
  ``canair.interop.explorer.export.export_profile``) plus an ``index.json`` that lists them.
  Decoded capture series go alongside: ``data/<profile>/sessions.json`` and one
  ``data/<profile>/s/<id>.json`` per session (``canair.interop.explorer.series``), which the page
  fetches only when it opens a session. The page that renders them is
  ``docs/profiles/explorer.md`` with ``docs/assets/explorer/explorer.{js,css}``.
- The JSON Schemas of the profile files. Each schema in ``canair.core.paths.EDITOR_SCHEMAS`` is
  served at ``schema/<name>.json``, the URL that a profile file's ``yaml-language-server``
  modeline names (``schema_modeline``) and the schema's own ``$id``. A schema written in YAML is
  published as JSON, which every editor reads.

Source links in the explorer data are pinned to the commit being built, so a line number cannot
drift when the YAML is edited later. A local build links to ``main``.

Plan: plans/2026-10-21-profile-explorer.md
"""

from __future__ import annotations

import json
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
PROFILES_DIR = REPO_ROOT / "profiles"
EXPLORER_DATA_DIR = "assets/explorer/data"
BLOB_ROOT = "https://github.com/philipkocanda/canair/blob/"


def blob_base(commit: str | None) -> str:
    """The URL prefix of the repository files at ``commit`` (``main`` when there is none)."""
    return f"{BLOB_ROOT}{commit or 'main'}/"


def _dump(data: dict | list) -> str:
    return json.dumps(data, ensure_ascii=False, separators=(",", ":"), sort_keys=False)


def explorer_files(commit: str | None = None) -> dict[str, str]:
    """The explorer's JSON files for every bundled profile, keyed by path in the site."""
    from canair.interop.explorer.export import export_profile
    from canair.interop.explorer.series import export_sessions, rx_by_address
    from canair.profile.model import looks_like_profile, profile_for_path

    files: dict[str, str] = {}
    index = []
    for root in (p for p in sorted(PROFILES_DIR.iterdir()) if looks_like_profile(p)):
        profile = profile_for_path(root)
        data = export_profile(root, blob_base=blob_base(commit), profile=profile)
        sessions, documents = export_sessions(root, rx_by_address(data), profile=profile)
        data["profile"]["sessions"] = len(sessions)
        files[f"{EXPLORER_DATA_DIR}/{root.name}.json"] = _dump(data)
        if sessions:
            files[f"{EXPLORER_DATA_DIR}/{root.name}/sessions.json"] = _dump(sessions)
            for session_id, doc in documents.items():
                files[f"{EXPLORER_DATA_DIR}/{root.name}/s/{session_id}.json"] = _dump(doc)
        index.append(
            {
                "name": root.name,
                "car_model": data["profile"]["car_model"],
                "maturity": data["profile"]["maturity"],
                "stats": data["profile"]["stats"],
                "sessions": len(sessions),
            }
        )
    files[f"{EXPLORER_DATA_DIR}/index.json"] = _dump(index)
    return files


def schema_files() -> dict[str, str]:
    """The editor JSON Schemas, keyed by path in the site."""
    from canair.core.json_schema import load_schema_file
    from canair.core.paths import DOCS_URL, EDITOR_SCHEMAS, schema_url

    return {
        schema_url(name).removeprefix(f"{DOCS_URL}/"): json.dumps(load_schema_file(path), indent=2)
        + "\n"
        for name, path in EDITOR_SCHEMAS.items()
    }
