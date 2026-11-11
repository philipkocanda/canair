"""MkDocs hook: the generated content of the docs site, built from the profile data.

The rendering lives in ``scripts/docs_site/`` and knows nothing about MkDocs. This file only
puts its output into a MkDocs build:

- ``on_files`` adds the bundled-profiles index page, the profile explorer's data and the editor
  JSON Schemas as virtual files. A committed copy of any of them fails the build.
- ``on_page_markdown`` fills the managed stats block and the meta description of each tour page.

Wired up by ``hooks:`` in ``mkdocs.yml``.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

from mkdocs.exceptions import PluginError
from mkdocs.structure.files import File, Files

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from scripts.docs_site import content, data  # noqa: E402


def on_files(files: Files, config) -> Files:
    generated = {
        content.INDEX_URI: content.render_index(content.discover_bundled()),
        **data.explorer_files(os.environ.get("GITHUB_SHA")),
        **data.schema_files(),
    }
    for uri, text in generated.items():
        if files.get_file_from_path(uri) is not None:
            raise PluginError(f"docs/{uri} is generated at build time; delete the committed copy.")
        files.append(File.generated(config, uri, content=text))
    return files


def on_page_markdown(markdown: str, page, config, files) -> str:
    uri = page.file.src_uri
    for name, page_uri in content.DEDICATED_PAGES.items():
        if uri == page_uri:
            root = content.PROFILES_DIR / name
            names = content.also_known_as_for(root)
            description = content.page_description(content.car_model(root), names)
            page.meta.setdefault("description", description)
            counts = content.profile_stats(root / "ecus")
            try:
                return content.fill_stats_block(markdown, counts, uri, names)
            except content.DocsContentError as error:
                raise PluginError(str(error)) from error
    return markdown
