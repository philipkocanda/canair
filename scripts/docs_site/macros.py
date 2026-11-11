"""The macros of the docs site: generated page content, answered while a page renders.

Zensical's ``macros`` plugin loads this file by path (``module_name`` in ``mkdocs.yml``) and calls
``define_env`` once for every page. A page asks for generated text with ``{{ macro() }}``. The
rendering is in ``content.py``; this file only hands it to the page.

A macro that cannot answer raises, and ``on_error_fail`` in ``mkdocs.yml`` then stops the build.
Nothing is cached, because ``zensical serve`` rebuilds in one process and must read the profile
data again.
"""

from __future__ import annotations

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from scripts.docs_site import content  # noqa: E402


def define_env(env) -> None:
    page_path = getattr(env.page, "path", None)
    for name, uri in content.DEDICATED_PAGES.items():
        if page_path == uri:
            env.page.meta.setdefault("description", content.tour_page_description(name))

    @env.macro
    def bundled_profiles_index() -> str:
        return content.render_index(content.discover_bundled())

    @env.macro
    def profile_stats_rows(name: str) -> str:
        return content.tour_page_rows(name)
