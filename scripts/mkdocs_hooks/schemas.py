"""MkDocs hook: publish the editor-facing JSON Schemas on the docs site.

``canlib/schema/profile_schema.json`` is served at ``schema/profile.json``, the URL every
``profile.yaml`` names in its ``yaml-language-server`` modeline
(``canlib.constants.PROFILE_SCHEMA_URL``). It is a virtual file, so the published schema always
matches the source and is never committed under ``docs/``.
"""

from __future__ import annotations

from mkdocs.exceptions import PluginError
from mkdocs.structure.files import File, Files


def on_files(files: Files, config) -> Files:
    from canlib.constants import DOCS_URL, PROFILE_SCHEMA_FILE, PROFILE_SCHEMA_URL

    uri = PROFILE_SCHEMA_URL.removeprefix(f"{DOCS_URL}/")
    if files.get_file_from_path(uri) is not None:
        raise PluginError(f"docs/{uri} is generated at build time by schemas.py; delete it.")
    files.append(File.generated(config, uri, content=PROFILE_SCHEMA_FILE.read_text()))
    return files
