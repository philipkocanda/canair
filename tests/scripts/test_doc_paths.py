"""scripts/check/doc_paths.py: which references in the agent documents count as stale."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "check" / "doc_paths.py"

spec = importlib.util.spec_from_file_location("doc_paths", SCRIPT)
assert spec and spec.loader
checker = importlib.util.module_from_spec(spec)
sys.modules["doc_paths"] = checker
spec.loader.exec_module(checker)

MODULE = """\
import os as operating_system
from pathlib import Path

LIMIT = 3
typed: int = 4

if LIMIT:
    CONDITIONAL = 1

try:
    import json
except ImportError:
    json = None


def top_level():
    inner = 1
    return inner


class Thing:
    ATTR = 1

    def method(self):
        pass
"""


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    module = tmp_path / "src" / "canair" / "pkg" / "mod.py"
    module.parent.mkdir(parents=True)
    module.write_text(MODULE, encoding="utf8")
    (tmp_path / "tests" / "pkg").mkdir(parents=True)
    (tmp_path / "tests" / "pkg" / "test_mod.py").write_text("", encoding="utf8")
    return tmp_path


def _scan(repo: Path, text: str) -> list[tuple[str, str]]:
    (repo / "AGENTS.md").write_text(text, encoding="utf8")
    return [(f.kind, f.reference) for f in checker.scan(repo)]


@pytest.mark.parametrize(
    "text",
    [
        "See `src/canair/pkg/mod.py`.",
        "See `src/canair/pkg/mod.py::top_level()` and `src/canair/pkg/mod.py::Thing`.",
        "See `src/canair/pkg/mod.py::LIMIT`, `src/canair/pkg/mod.py::typed`.",
        "See `src/canair/pkg/mod.py::CONDITIONAL` and `src/canair/pkg/mod.py::json`.",
        "See `src/canair/pkg/mod.py::operating_system` and `src/canair/pkg/mod.py::Path`.",
        "See `src/canair/pkg/mod.py::Thing.method` and `src/canair/pkg/mod.py::Thing.ATTR`.",
        "The test is `tests/pkg/test_mod.py`, in `tests/pkg/`.",
        "A sentence that ends with `tests/pkg/test_mod.py`.",
        "It ends the sentence at src/canair/pkg/mod.py.",
    ],
)
def test_live_references_pass(repo: Path, text: str) -> None:
    assert _scan(repo, text) == []


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("`src/canair/pkg/gone.py`", [("missing file", "src/canair/pkg/gone.py")]),
        ("`src/canair/mod.py` moved", [("missing file", "src/canair/mod.py")]),
        (
            "`src/canair/pkg/mod.py::inner`",
            [("missing symbol", "src/canair/pkg/mod.py::inner")],
        ),
        (
            "`src/canair/pkg/mod.py::Thing.gone`",
            [("missing symbol", "src/canair/pkg/mod.py::Thing.gone")],
        ),
        ("`src/canair/pkg/gone.py::top_level`", [("missing file", "src/canair/pkg/gone.py")]),
        ("`tests/pkg/test_gone.py`", [("missing test", "tests/pkg/test_gone.py")]),
        ("`tests/gone/`", [("missing test", "tests/gone/")]),
    ],
)
def test_stale_references_are_reported(repo: Path, text: str, expected) -> None:
    assert _scan(repo, text) == expected


@pytest.mark.parametrize(
    "text",
    [
        "`src/canair/<pkg>/mod.py`",
        "`src/canair/*/mod.py`",
        "`src/canair/pkg/{mod,gone}.py`",
        "`src/canair/pkg/gone_*.py`",
        "`tests/<dir>/test_x.py`",
        "`tests/pkg/test_*.py`",
        "`tests/…`",
        "the tests/ directory",
        "`src/canair/pkg/mod.pyc`",
        "an other-repo/src/canair/gone.py path",
    ],
)
def test_patterns_and_placeholders_are_skipped(repo: Path, text: str) -> None:
    assert _scan(repo, text) == []


def test_finding_names_document_and_line(repo: Path) -> None:
    (repo / "AGENTS.md").write_text("fine\n\n`tests/gone.py`\n", encoding="utf8")
    [finding] = checker.scan(repo)
    assert str(finding) == "AGENTS.md:3: missing test: tests/gone.py"


def test_documents_in_scope(repo: Path) -> None:
    for name in (
        "AGENTS.md",
        ".claude/skills/one/SKILL.md",
        ".claude/skills/one/extra.md",
        "docs/development/page.md",
        "docs/other/page.md",
        "plans/old.md",
        "CHANGELOG.md",
    ):
        path = repo / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("`tests/gone.py`\n", encoding="utf8")
    scanned = {f.doc for f in checker.scan(repo)}
    assert scanned == {
        "AGENTS.md",
        ".claude/skills/one/SKILL.md",
        ".claude/skills/one/extra.md",
        "docs/development/page.md",
    }


def test_the_repository_documents_are_clean() -> None:
    assert [str(f) for f in checker.scan(checker.REPO_ROOT)] == []
