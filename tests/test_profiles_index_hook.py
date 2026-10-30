"""Tests for the build-time bundled-profiles MkDocs hook (scripts/mkdocs_hooks/)."""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

pytest.importorskip("mkdocs")

REPO_ROOT = Path(__file__).resolve().parent.parent
HOOK = REPO_ROOT / "scripts" / "mkdocs_hooks" / "profiles_index.py"


@pytest.fixture(scope="module")
def mod():
    spec = importlib.util.spec_from_file_location("profiles_index_hook", HOOK)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _counts(ecus: int, params: int, verified: int) -> dict[str, int]:
    return {"ecus": ecus, "pids": 1, "params": params, "verified": verified}


@pytest.mark.parametrize(
    ("counts", "expected"),
    [
        (_counts(16, 163, 8), "An early profile (163 signals, 8 verified) — a starting point"),
        (_counts(1, 1, 1), "A seed / stub profile (1 signal, 1 verified) — a starting point"),
        (_counts(1, 194, 194), "A mature profile (194 signals, 194 verified). Browse"),
    ],
)
def test_profile_summary_names_maturity_once(mod, counts, expected):
    """Regression: the sentence read "An early early profile" / "An early mature profile"."""
    summary = mod.profile_summary(counts, "SRC")
    assert summary.startswith(expected)
    assert summary.endswith("Browse SRC.")


def test_mature_profile_is_not_called_a_starting_point(mod):
    assert "starting point" not in mod.profile_summary(_counts(1, 194, 194), "SRC")


def _write_profile(root: Path) -> Path:
    (root / "ecus").mkdir(parents=True)
    (root / "profile.yaml").write_text('car_model: "Test Car"\n')
    (root / "ecus" / "bms.yaml").write_text(
        "BMS:\n"
        "  tx_id: 0x7E4\n"
        "  pids:\n"
        "    '2101':\n"
        "      parameters:\n"
        "        SOC: {expression: 'B10/2', verified: true}\n"
        "        VOLT: {expression: 'B11'}\n"
        "  research:\n"
        "    - {title: open lead}\n"
        "    - {title: closed lead, status: done}\n"
    )
    return root


def test_counts_come_from_the_profile_data(mod, tmp_path):
    counts = mod.profile_stats(_write_profile(tmp_path / "test-car") / "ecus")
    assert counts == {
        "ecus": 1,
        "pids": 1,
        "params": 2,
        "verified": 1,
        "iocontrol": 0,
        "research": 1,
    }


def test_index_lists_every_bundled_profile(mod, tmp_path):
    _write_profile(tmp_path / "test-car")
    page = mod.render_index(mod.discover_bundled(tmp_path))
    assert "| `test-car` | Test Car | 1 | 1 | 2 | 1 |" in page


def test_dedicated_page_links_resolve_from_the_index(mod):
    """The index's links to a tour page are relative to profiles/index.md."""
    assert mod._page_link("profiles/hyundai-ioniq-ae-ev.md") == "hyundai-ioniq-ae-ev.md"


def test_index_links_every_dedicated_page(mod):
    index = mod.render_index(mod.discover_bundled())
    for name, page_uri in mod.DEDICATED_PAGES.items():
        assert f"[{name}]({mod._page_link(page_uri)})" in index
        assert (REPO_ROOT / "docs" / page_uri).exists()


def test_stats_block_replaces_the_placeholder(mod):
    source = (
        "| Vehicle | x | <!-- BEGIN generated-stats -->\n"
        "| Counts | *placeholder* | <!-- END generated-stats -->\n"
        "\nprose\n"
    )
    counts = {"ecus": 3, "pids": 4, "params": 5, "verified": 2, "iocontrol": 6, "research": 7}
    out = mod.fill_stats_block(source, counts, "p.md")
    assert "placeholder" not in out
    assert "| Signals | **5** decoded (**2 verified**, 3 unverified/candidate) |" in out
    assert out.endswith("<!-- END generated-stats -->\n\nprose\n")


def test_page_without_markers_fails_the_build(mod):
    from mkdocs.exceptions import PluginError

    with pytest.raises(PluginError, match="generated-stats"):
        mod.fill_stats_block("no markers here", _counts(1, 1, 1), "p.md")


def test_every_dedicated_page_carries_the_markers(mod):
    for page_uri in mod.DEDICATED_PAGES.values():
        text = (REPO_ROOT / "docs" / page_uri).read_text()
        assert mod.STATS_BEGIN in text and mod.STATS_END in text, page_uri


def test_index_page_is_not_committed(mod):
    """The hook generates it; a committed copy would make the build fail."""
    assert not (REPO_ROOT / "docs" / mod.INDEX_URI).exists()
