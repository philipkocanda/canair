"""MkDocs hook: publish the JSON Schemas of the profile files on the docs site.

Each schema in ``canair.core.paths.EDITOR_SCHEMAS`` is served at ``schema/<name>.json``, the URL
that a profile file's ``yaml-language-server`` modeline names (``schema_modeline``) and the
schema's own ``$id``. A schema written in YAML is published as JSON, which every editor reads.
They are virtual files, so the published schemas always match the source and are never committed
under ``docs/``.
"""

from __future__ import annotations

import json

from mkdocs.exceptions import PluginError
from mkdocs.structure.files import File, Files


def on_files(files: Files, config) -> Files:
    from canair.core.json_schema import load_schema_file
    from canair.core.paths import DOCS_URL, EDITOR_SCHEMAS, schema_url

    for name, path in EDITOR_SCHEMAS.items():
        uri = schema_url(name).removeprefix(f"{DOCS_URL}/")
        if files.get_file_from_path(uri) is not None:
            raise PluginError(f"docs/{uri} is generated at build time by schemas.py; delete it.")
        content = json.dumps(load_schema_file(path), indent=2) + "\n"
        files.append(File.generated(config, uri, content=content))
    return files
