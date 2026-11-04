"""The architecture page is generated from the layers contract and the package charters."""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "gen" / "gen_architecture.py"


@pytest.fixture(scope="module")
def gen():
    spec = importlib.util.spec_from_file_location("gen_architecture", SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_the_committed_page_is_current(gen):
    page = gen.PAGE.read_text(encoding="utf8")
    assert gen.render(page) == page, "run `python3 scripts/gen/gen_architecture.py`"


def test_a_charter_is_read_from_the_package_docstring(gen, tmp_path):
    package = tmp_path / "demo"
    package.mkdir()
    (package / "__init__.py").write_text(
        '"""Demo package.\n\nBelongs: things.\nNot here: other\nthings.\nImports: core.\n"""\n'
    )
    assert gen.charter(package) == {
        "summary": "Demo package.",
        "Belongs": "things.",
        "Not here": "other things.",
        "Imports": "core.",
    }


def test_the_layers_table_lists_every_top_level_package(gen):
    table = gen.render_layers()
    packages = {p.name for p in gen.PACKAGE_DIR.iterdir() if (p / "__init__.py").is_file()}
    assert {f"`{name}/`" for name in packages} <= set(table.replace(",", " ").split())
