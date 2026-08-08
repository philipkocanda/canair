"""Tests for scripts/validate_skills.py.

Covers the frontmatter parser and the per-field validation rules (the
intersection of OpenCode's and the Agent Skills spec's requirements), plus an
integration check that every bundled `.claude/skills/*/SKILL.md` currently
passes.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
SCRIPT = REPO_ROOT / "scripts" / "validate_skills.py"


@pytest.fixture(scope="module")
def mod():
    spec = importlib.util.spec_from_file_location("validate_skills", SCRIPT)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def write_skill(tmp_path: Path, dir_name: str, frontmatter: str) -> Path:
    skill_dir = tmp_path / dir_name
    skill_dir.mkdir(parents=True)
    path = skill_dir / "SKILL.md"
    path.write_text(f"---\n{frontmatter}\n---\n\nbody\n")
    return path


def test_valid_frontmatter_has_no_errors(mod, tmp_path):
    path = write_skill(tmp_path, "my-skill", "name: my-skill\ndescription: does a thing")
    text = path.read_text()
    data, parse_errors = mod.parse_frontmatter(text, path)
    assert parse_errors == []
    assert mod.validate_frontmatter(data, path, dir_name="my-skill") == []


def test_missing_frontmatter_delimiter(mod, tmp_path):
    path = tmp_path / "SKILL.md"
    path.write_text("# no frontmatter\n")
    _, errors = mod.parse_frontmatter(path.read_text(), path)
    assert any("missing YAML frontmatter" in e for e in errors)


def test_unclosed_frontmatter(mod, tmp_path):
    path = tmp_path / "SKILL.md"
    path.write_text("---\nname: x\n")
    _, errors = mod.parse_frontmatter(path.read_text(), path)
    assert any("no closing" in e for e in errors)


def test_invalid_yaml_is_reported(mod, tmp_path):
    # A plain scalar continued on an unindented line is not valid YAML, even
    # though it looks harmless — this is the real bug found in
    # contributing-code/SKILL.md before it was fixed.
    path = write_skill(
        tmp_path,
        "broken",
        "name: broken\ndescription: first line\nsecond line with no indent",
    )
    _, errors = mod.parse_frontmatter(path.read_text(), path)
    assert any("invalid YAML" in e for e in errors)


def test_frontmatter_must_be_a_mapping(mod, tmp_path):
    path = tmp_path / "SKILL.md"
    path.write_text("---\n- just\n- a\n- list\n---\n")
    _, errors = mod.parse_frontmatter(path.read_text(), path)
    assert any("must be a YAML mapping" in e for e in errors)


@pytest.mark.parametrize("name", ["my-skill", "a", "a1-b2", "x" * 64])
def test_valid_names_accepted(mod, name):
    data = {"name": name, "description": "d"}
    errors = mod.validate_frontmatter(data, Path("SKILL.md"), dir_name=name)
    assert errors == []


@pytest.mark.parametrize(
    "name",
    [
        "My-Skill",  # uppercase
        "my_skill",  # underscore
        "-my-skill",  # leading hyphen
        "my-skill-",  # trailing hyphen
        "my--skill",  # doubled hyphen
        "x" * 65,  # too long
        "",  # empty
    ],
)
def test_invalid_names_rejected(mod, name):
    data = {"name": name, "description": "d"}
    errors = mod.validate_frontmatter(data, Path("SKILL.md"), dir_name=name or "x")
    assert errors


def test_name_must_match_directory(mod):
    data = {"name": "foo", "description": "d"}
    errors = mod.validate_frontmatter(data, Path("SKILL.md"), dir_name="bar")
    assert any("must match its directory name" in e for e in errors)


def test_missing_required_fields(mod):
    errors = mod.validate_frontmatter({}, Path("SKILL.md"), dir_name="x")
    assert any("missing required field(s)" in e for e in errors)


def test_unknown_field_rejected(mod):
    data = {"name": "x", "description": "d", "disable-model-invocation": True}
    errors = mod.validate_frontmatter(data, Path("SKILL.md"), dir_name="x")
    assert any("unrecognized frontmatter field(s)" in e for e in errors)


@pytest.mark.parametrize("field", ["license", "compatibility", "allowed-tools", "metadata"])
def test_optional_spec_fields_accepted(mod, field):
    data = {"name": "x", "description": "d", field: "value" if field != "metadata" else {"a": "b"}}
    if field == "allowed-tools":
        data[field] = ["Read", "Grep"]
    errors = mod.validate_frontmatter(data, Path("SKILL.md"), dir_name="x")
    assert errors == []


def test_description_too_long_rejected(mod):
    data = {"name": "x", "description": "y" * 1025}
    errors = mod.validate_frontmatter(data, Path("SKILL.md"), dir_name="x")
    assert any("'description'" in e for e in errors)


def test_compatibility_too_long_rejected(mod):
    data = {"name": "x", "description": "d", "compatibility": "y" * 501}
    errors = mod.validate_frontmatter(data, Path("SKILL.md"), dir_name="x")
    assert any("'compatibility'" in e for e in errors)


def test_metadata_must_be_string_map(mod):
    data = {"name": "x", "description": "d", "metadata": {"a": 1}}
    errors = mod.validate_frontmatter(data, Path("SKILL.md"), dir_name="x")
    assert any("'metadata'" in e for e in errors)


def test_allowed_tools_rejects_non_string_list(mod):
    data = {"name": "x", "description": "d", "allowed-tools": [1, 2]}
    errors = mod.validate_frontmatter(data, Path("SKILL.md"), dir_name="x")
    assert any("'allowed-tools'" in e for e in errors)


def test_bundled_skills_all_pass(mod):
    files = mod.find_skill_files()
    assert files, "no bundled SKILL.md files found"
    all_errors: list[str] = []
    for path in files:
        all_errors.extend(mod.validate_skill_file(path))
    assert all_errors == [], all_errors


def test_main_returns_zero_for_bundled_skills(mod):
    assert mod.main([]) == 0
