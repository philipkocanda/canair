"""Tests for ``also_known_as:`` — the other names owners call a car by.

They live in ``profile.yaml`` (the whole car) and ``variants.yaml`` (one variant), are
read by ``canair.profile.names.also_known_as``, and are rendered by the docs site build so a
search for "Classic Ioniq" finds the profile. Labels only: nothing resolves them.
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest
import yaml

from canair.profile.names import AlsoKnownAs, VariantNames, also_known_as, duplicate_names
from canair.validation.profile_names import profile_name_findings
from canair.variants import check_variants_doc
from scripts.docs_site import content

REPO_ROOT = Path(__file__).resolve().parents[2]


def _bundle(root: Path, profile: str = "", variants: str = "") -> Path:
    root.mkdir(parents=True, exist_ok=True)
    (root / "profile.yaml").write_text('car_model: "Test Car"\ninit: "ATSP6;"\n' + profile)
    if variants:
        (root / "variants.yaml").write_text(variants)
    return root


class TestReader:
    def test_reads_profile_and_variant_names(self, tmp_path):
        root = _bundle(
            tmp_path / "car",
            "also_known_as: [Classic Car, Car EV]\n",
            "variants:\n  28kwh:\n    name: Car 28 kWh\n    also_known_as: [Car 28]\n"
            "  38kwh:\n    name: Car 38 kWh\n",
        )
        names = also_known_as(root)
        assert names.profile == ("Classic Car", "Car EV")
        assert names.variants == (VariantNames("28kwh", "Car 28 kWh", ("Car 28",)),)

    def test_a_bundle_without_names_has_none(self, tmp_path):
        assert also_known_as(_bundle(tmp_path / "car")) == AlsoKnownAs()
        assert also_known_as(tmp_path / "missing") == AlsoKnownAs()

    def test_malformed_entries_are_ignored_not_fatal(self, tmp_path):
        root = _bundle(
            tmp_path / "car",
            "also_known_as: [ok, 7, '', ' padded ']\n",
            "variants:\n  a:\n    name: A\n    also_known_as: nope\n",
        )
        names = also_known_as(root)
        assert names.profile == ("ok", "padded")
        assert names.variants == ()

    def test_all_dedupes_ignoring_case_and_puts_the_profile_first(self):
        names = AlsoKnownAs(
            ("Ioniq EV", "Classic Ioniq"),
            (VariantNames("a", "A", ("ioniq ev", "Ioniq 28")),),
        )
        assert names.all() == ["Ioniq EV", "Classic Ioniq", "Ioniq 28"]

    def test_duplicate_names(self):
        assert duplicate_names(["A", "B", "a", "A"]) == ["a", "A"]


class TestValidation:
    def _findings(self, tmp_path, names):
        (tmp_path / "profile.yaml").write_text(yaml.safe_dump({"also_known_as": names}))
        prof = SimpleNamespace(
            name="car-model-x-ev", root=tmp_path, meta_file=tmp_path / "profile.yaml"
        )
        return profile_name_findings(prof)[0]

    def test_a_list_of_names_is_clean(self, tmp_path):
        assert self._findings(tmp_path, ["Classic Ioniq", "Ioniq 28"]) == []

    @pytest.mark.parametrize("bad", ["Classic Ioniq", [1], [""], {"a": 1}])
    def test_must_be_a_list_of_text(self, tmp_path, bad):
        from canair.core.json_schema import schema_errors
        from canair.core.paths import PROFILE_SCHEMA_FILE

        doc = {"car_model": "X", "init": "", "also_known_as": bad}
        assert any(e.startswith("also_known_as") for e in schema_errors(PROFILE_SCHEMA_FILE, doc))
        assert self._findings(tmp_path, bad) == []  # the schema's to report

    def test_a_repeated_name_is_an_error(self, tmp_path):
        errors = self._findings(tmp_path, ["Ioniq 28", "ioniq 28"])
        assert any("lists ioniq 28 twice" in e for e in errors)

    def test_variant_names_are_checked_as_a_list_of_text(self):
        doc = {"variants": {"a": {"name": "A", "also_known_as": "Ioniq 28"}}}
        assert any("also_known_as: must be a list" in e for e in check_variants_doc(doc))
        doc["variants"]["a"]["also_known_as"] = ["Ioniq 28"]
        assert check_variants_doc(doc) == []


@pytest.fixture(scope="module")
def hook():
    return content


class TestDocsPage:
    NAMES = AlsoKnownAs(
        ("Classic Ioniq",), (VariantNames("38kwh", "Ioniq 38 kWh", ("Ioniq vFL", "Ioniq FL")),)
    )

    def test_the_stats_block_gains_a_row_with_the_names(self, hook):
        counts = {"ecus": 1, "pids": 1, "params": 1, "verified": 1, "iocontrol": 0, "research": 0}
        block = hook.render_stats_block(counts, self.NAMES)
        assert "| Also known as | Classic Ioniq<br>**Ioniq 38 kWh**: Ioniq vFL, Ioniq FL |" in block
        # The BEGIN marker still ends the hand-written row above, so the table stays whole.
        assert block.startswith(hook.STATS_BEGIN + "\n| Also known as")

    def test_no_names_means_no_row(self, hook):
        counts = {"ecus": 1, "pids": 1, "params": 1, "verified": 1, "iocontrol": 0, "research": 0}
        assert "Also known as" not in hook.render_stats_block(counts, AlsoKnownAs())
        assert "Also known as" not in hook.render_stats_block(counts)

    def test_a_pipe_in_a_name_cannot_split_the_table(self, hook):
        names = AlsoKnownAs(("A | B",))
        assert "A \\| B" in hook.known_as_cell(names)

    def test_the_description_carries_every_name(self, hook):
        text = hook.page_description("Hyundai Ioniq Electric (AE)", self.NAMES)
        assert "Hyundai Ioniq Electric (AE)" in text
        assert "Classic Ioniq, Ioniq vFL, Ioniq FL" in text
        assert hook.page_description("Car", AlsoKnownAs()) == "canair vehicle profile for the Car."

    def test_the_index_lists_names_for_a_bundled_profile(self, hook, tmp_path):
        _bundle(tmp_path / "test-car", "also_known_as: [Classic Car]\n")
        page = hook.render_index(hook.discover_bundled(tmp_path))
        assert "Test Car. Also known as Classic Car." in page


def test_the_bundled_ioniq_answers_to_the_names_owners_use():
    names = also_known_as(REPO_ROOT / "profiles" / "hyundai-ioniq-ae-ev").all()
    for wanted in ("Classic Ioniq", "Ioniq 28", "Ioniq vFL"):
        assert wanted in names
