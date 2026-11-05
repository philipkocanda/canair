"""Every profile file names its schema on line 1 and conforms to it.

The schemas in ``core.paths.EDITOR_SCHEMAS`` are what ``canair validate`` checks and what editors
read through the ``yaml-language-server`` modeline, so a bundled or fixture file, a scaffolded
one and one an editor creates must all carry the modeline and pass.
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from canair.core.json_schema import schema_errors
from canair.core.paths import BUNDLED_PROFILES_DIR, EDITOR_SCHEMAS, schema_modeline

FIXTURE_PROFILES_DIR = Path(__file__).parents[1] / "fixtures" / "profiles"
FILES = {
    "vehicle_states.yaml": "vehicle_states",
    "can_buses.yaml": "can_buses",
    "groups.yaml": "groups",
    "variants.yaml": "variants",
}


def _profile_files(root: Path) -> list[tuple[Path, str]]:
    found = [(root / name, schema) for name, schema in FILES.items() if (root / name).exists()]
    return found + [(p, "signals") for p in sorted((root / "signals").glob("*.yaml"))]


PROFILES = sorted(
    p.parent
    for p in [
        *BUNDLED_PROFILES_DIR.glob("*/profile.yaml"),
        *FIXTURE_PROFILES_DIR.glob("*/profile.yaml"),
    ]
)
CASES = [(path, schema) for root in PROFILES for path, schema in _profile_files(root)]


@pytest.mark.parametrize(
    ("path", "schema"), CASES, ids=[f"{p.parent.name}/{p.name}" for p, _ in CASES]
)
def test_file_names_its_schema_and_conforms(path, schema):
    text = path.read_text()
    assert text.splitlines()[0] == schema_modeline(schema)
    assert schema_errors(EDITOR_SCHEMAS[schema], yaml.safe_load(text)) == []


def test_scaffold_files_name_their_schemas(tmp_path):
    from canair.profile.scaffold import create_profile

    root = create_profile("testcar", car_model="Test Car", path=tmp_path / "p")
    files = _profile_files(root)
    assert {p.name for p, _ in files} == set(FILES)
    for path, schema in files:
        text = path.read_text()
        assert text.splitlines()[0] == schema_modeline(schema)
        assert schema_errors(EDITOR_SCHEMAS[schema], yaml.safe_load(text)) == []


@pytest.mark.parametrize("schema", ["vehicle_states", "can_buses", "groups"])
def test_an_unknown_top_level_key_is_an_error(schema):
    key = {"vehicle_states": "states", "can_buses": "can_buses", "groups": "groups"}[schema]
    empty = [] if schema == "vehicle_states" else {}
    errors = schema_errors(EDITOR_SCHEMAS[schema], {key: empty, "stats": []})
    assert errors and errors[0].startswith("unknown field 'stats'")
