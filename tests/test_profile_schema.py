"""The editor-facing profile.yaml JSON Schema stays true to canair's own validation.

``canair validate`` (``validate_meta``) is the authoritative check; the schema only
serves editors. These tests pin the two together so an editor never flags a profile
canair accepts, or offers a quirk/mode/field canair rejects.
"""

from __future__ import annotations

import json
from pathlib import Path

import jsonschema
import pytest
import yaml

from canlib.addressing import AddressingMode
from canlib.commands.validate.pids import _ADDRESSING_FIELDS
from canlib.constants import (
    BUNDLED_PROFILES_DIR,
    PROFILE_SCHEMA_FILE,
    PROFILE_SCHEMA_MODELINE,
)
from canlib.profile_create import create_profile
from canlib.quirks import KNOWN_QUIRKS
from canlib.transport.isotp_params import ISOTP_FIELDS

FIXTURE_PROFILES_DIR = Path(__file__).parent / "fixtures" / "profiles"
PROFILE_FILES = sorted(
    [*BUNDLED_PROFILES_DIR.glob("*/profile.yaml"), *FIXTURE_PROFILES_DIR.glob("*/profile.yaml")]
)

SCHEMA = json.loads(PROFILE_SCHEMA_FILE.read_text())
PROPS = SCHEMA["properties"]


def _errors(doc: dict) -> list[str]:
    validator = jsonschema.Draft7Validator(SCHEMA)
    return [e.message for e in validator.iter_errors(doc)]


def test_schema_is_a_valid_draft7_schema():
    jsonschema.Draft7Validator.check_schema(SCHEMA)


@pytest.mark.parametrize("path", PROFILE_FILES, ids=lambda p: p.parent.name)
def test_profile_yaml_names_the_schema(path):
    assert path.read_text().splitlines()[0] == PROFILE_SCHEMA_MODELINE


@pytest.mark.parametrize("path", PROFILE_FILES, ids=lambda p: p.parent.name)
def test_profile_yaml_conforms(path):
    assert _errors(yaml.safe_load(path.read_text())) == []


def test_scaffold_conforms(tmp_path):
    root = create_profile("testcar", car_model="Test Car", path=tmp_path / "p")
    assert _errors(yaml.safe_load((root / "profile.yaml").read_text())) == []


def test_quirks_match_known_quirks():
    assert set(PROPS["quirks"]["items"]["enum"]) == set(KNOWN_QUIRKS)


def test_addressing_matches_validator():
    addressing = PROPS["addressing"]
    assert set(addressing["properties"]) == _ADDRESSING_FIELDS
    assert set(addressing["properties"]["mode"]["enum"]) == {m.value for m in AddressingMode}


def test_isotp_fields_match_validator():
    assert set(PROPS["isotp"]["properties"]) == set(ISOTP_FIELDS)


def test_unknown_top_level_keys_are_tolerated():
    assert _errors({"car_model": "X", "init": "", "future_setting": 1}) == []


def test_an_overlay_needs_no_car_model():
    assert _errors({"extends": "base"}) == []
    assert _errors({}) != []


@pytest.mark.parametrize(
    "doc",
    [
        {"quirks": ["not_a_quirk"]},
        {"isotp": {"tx_pading": 0x55}},
        {"addressing": {"mode": "normal_12bit"}},
        {"isotp": {"tx_padding": 256}},
    ],
)
def test_rejects_what_validate_rejects(doc):
    assert _errors({"car_model": "X", "init": "", **doc}) != []


@pytest.mark.parametrize("rx_offset", [8, -0x20, "-0x20"])
def test_rx_offset_may_be_negative_hex(rx_offset):
    # YAML 1.2 (the editor) reads `-0x20` as a string; PyYAML reads it as an int.
    assert _errors({"car_model": "X", "init": "", "addressing": {"rx_offset": rx_offset}}) == []
