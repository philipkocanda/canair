"""Tests for the bundled-profile pages that the docs site renders (scripts/docs_site/)."""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from scripts.docs_site import content, data, macros

REPO_ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture(scope="module")
def mod():
    return content


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
    assert mod.page_link("profiles/hyundai-ioniq-ae-ev.md") == "hyundai-ioniq-ae-ev.md"


def test_index_links_every_dedicated_page(mod):
    index = mod.render_index(mod.discover_bundled())
    for name, page_uri in mod.DEDICATED_PAGES.items():
        assert f"[{name}]({mod.page_link(page_uri)})" in index
        assert (REPO_ROOT / "docs" / page_uri).exists()


def test_stats_rows_come_from_the_counts(mod):
    counts = {"ecus": 3, "pids": 4, "params": 5, "verified": 2, "iocontrol": 6, "research": 7}
    rows = mod.render_stats_rows(counts).splitlines()
    assert "| Signals | **5** decoded (**2 verified**, 3 unverified/candidate) |" in rows
    assert all(row.startswith("| ") and row.endswith(" |") for row in rows)


def test_every_dedicated_page_asks_for_its_own_rows(mod):
    for name, page_uri in mod.DEDICATED_PAGES.items():
        text = (REPO_ROOT / "docs" / page_uri).read_text()
        assert f'{{{{ profile_stats_rows("{name}") }}}}' in text, page_uri


def test_the_index_page_asks_for_the_generated_index(mod):
    text = (REPO_ROOT / "docs" / mod.INDEX_URI).read_text()
    assert "{{ bundled_profiles_index() }}" in text


def test_a_profile_without_a_tour_page_cannot_be_asked_for_one(mod):
    with pytest.raises(mod.DocsContentError, match="no tour page"):
        mod.tour_page_rows("no-such-profile")


class FakeEnv:
    def __init__(self, path: str):
        self.page = SimpleNamespace(path=path, meta={})
        self.macros: dict = {}

    def macro(self, function):
        self.macros[function.__name__] = function
        return function


def _env_for(path: str) -> FakeEnv:
    env = FakeEnv(path)
    macros.define_env(env)
    return env


def test_macros_answer_with_the_rendered_content():
    env = _env_for("profiles/index.md")
    assert env.macros["bundled_profiles_index"]().startswith("The repo ships")
    name = next(iter(content.DEDICATED_PAGES))
    assert env.macros["profile_stats_rows"](name) == content.tour_page_rows(name)


def test_a_tour_page_gets_its_description_but_keeps_one_it_sets():
    name, uri = next(iter(content.DEDICATED_PAGES.items()))
    assert _env_for(uri).page.meta["description"] == content.tour_page_description(name)
    other = FakeEnv(uri)
    other.page.meta["description"] = "mine"
    macros.define_env(other)
    assert other.page.meta["description"] == "mine"


def test_other_pages_get_no_description():
    assert _env_for("concepts/profiles.md").page.meta == {}


def test_write_all_replaces_the_generated_directories(tmp_path):
    stale = tmp_path / data.SCHEMA_DIR / "gone.json"
    stale.parent.mkdir()
    stale.write_text("{}")
    count = data.write_all(tmp_path)
    assert not stale.exists()
    assert (tmp_path / data.EXPLORER_DATA_DIR / "index.json").is_file()
    assert (tmp_path / data.SCHEMA_DIR / "profile.json").is_file()
    assert count == sum(1 for p in tmp_path.rglob("*") if p.is_file())


def test_schemas_are_published_as_json_at_the_url_the_modeline_names():
    from canair.core.paths import EDITOR_SCHEMAS, schema_url

    files = data.schema_files()
    assert len(files) == len(EDITOR_SCHEMAS)
    for name in EDITOR_SCHEMAS:
        uri = schema_url(name).rsplit("/", 2)[-2:]
        text = files["/".join(uri)]
        assert json.loads(text)["$id"] == schema_url(name)
        assert text.endswith("}\n")


def test_blob_links_are_pinned_to_the_commit_being_built():
    assert data.blob_base("abc123").endswith("/blob/abc123/")
    assert data.blob_base(None).endswith("/blob/main/")
    assert data.blob_base("").endswith("/blob/main/")
