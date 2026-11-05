"""core.json_schema: how a schema finding reads, and that canair's schemas resolve."""

from __future__ import annotations

import json

import pytest

from canair.core import json_schema
from canair.core.json_schema import check_schema_file, load_schema_file, schema_errors
from canair.core.paths import EDITOR_SCHEMAS, SCHEMA_DIR, schema_url


@pytest.fixture
def schema(tmp_path):
    """Write a draft-07 schema and return its path."""

    def write(body: dict):
        path = tmp_path / "s.json"
        path.write_text(json.dumps({"$schema": "http://json-schema.org/draft-07/schema#", **body}))
        json_schema._validator.cache_clear()
        return path

    return write


class TestMessages:
    def test_unknown_and_missing_fields(self, schema):
        path = schema(
            {
                "type": "object",
                "required": ["name"],
                "additionalProperties": False,
                "properties": {"name": {"type": "string"}, "unit": {"type": "string"}},
            }
        )
        assert schema_errors(path, {"colour": 1}) == [
            "missing required field 'name'",
            "unknown field 'colour' (allowed: name, unit)",
        ]

    def test_types_ranges_and_enums(self, schema):
        path = schema(
            {
                "properties": {
                    "n": {"type": "integer", "minimum": 1},
                    "rate": {"type": "integer", "exclusiveMinimum": 0},
                    "order": {"enum": ["little", "big"]},
                    "pair": {"type": "array", "minItems": 2, "maxItems": 2},
                    "flag": {"type": "boolean"},
                }
            }
        )
        doc = {"n": 0, "rate": "x", "order": "middle", "pair": [1], "flag": 1}
        assert schema_errors(path, doc) == [
            "flag: must be true or false (got 1)",
            "n: must be at least 1 (got 0)",
            "order: must be one of 'little', 'big' (got 'middle')",
            "pair: must hold exactly 2 items",
            "rate: must be an integer (got 'x')",
        ]

    def test_a_boolean_where_text_belongs_names_the_yaml_trap(self, schema):
        path = schema({"properties": {"name": {"type": "string"}, "n": {"type": "integer"}}})
        assert schema_errors(path, {"name": True, "n": True}) == [
            "n: must be an integer (got true)",
            "name: must be text (got true: YAML reads an unquoted ON, OFF, YES or NO as true or "
            "false, so quote it)",
        ]

    def test_paths_are_dotted_with_list_indices(self, schema):
        item = {"type": "object", "properties": {"v": {"type": "integer"}}}
        path = schema({"properties": {"a b": {"type": "array", "items": item}}})
        assert schema_errors(path, {"a b": [{"v": 1}, {"v": "x"}]}) == [
            "'a b'[1].v: must be an integer (got 'x')"
        ]

    def test_error_messages_from_the_schema(self, schema):
        path = schema(
            {
                "properties": {
                    "id": {"type": "string", "pattern": "^0x", "patternErrorMessage": "not hex"},
                    "scale": {"not": {"const": 0}, "errorMessage": "must not be 0"},
                }
            }
        )
        assert schema_errors(path, {"id": "12", "scale": 0}) == [
            "id: not hex",
            "scale: must not be 0",
        ]

    def test_a_key_rule_names_the_key(self, schema):
        path = schema({"propertyNames": {"pattern": "^[a-z]+$"}})
        assert schema_errors(path, {"ok": 1, "Bad": 2}) == ["Bad: must match ^[a-z]+$ (got 'Bad')"]

    def test_keys_are_checked_as_text(self, schema):
        # YAML reads `0: off` with an integer key; JSON Schema knows only string keys.
        path = schema({"propertyNames": {"pattern": "^[0-9]+$"}})
        assert schema_errors(path, {0: "off", 1: "on"}) == []
        assert schema_errors(path, {True: "x"}) == ["true: must match ^[0-9]+$ (got 'true')"]

    def test_any_of_reports_its_best_branch(self, schema):
        path = schema({"properties": {"v": {"anyOf": [{"type": "integer"}, {"type": "null"}]}}})
        assert len(schema_errors(path, {"v": "x"})) == 1


class TestCanairSchemas:
    @pytest.mark.parametrize("path", sorted(SCHEMA_DIR.glob("*_schema.*")), ids=lambda p: p.name)
    def test_every_machine_schema_is_valid(self, path):
        if "$schema" not in load_schema_file(path):
            pytest.skip("a field list, not a JSON Schema yet")
        check_schema_file(path)

    @pytest.mark.parametrize("name", sorted(EDITOR_SCHEMAS))
    def test_each_editor_schema_is_published_at_its_id(self, name):
        doc = load_schema_file(EDITOR_SCHEMAS[name])
        assert doc["$schema"] == "http://json-schema.org/draft-07/schema#"
        assert doc["$id"] == schema_url(name)

    def test_a_reference_into_another_schema_resolves(self):
        doc = {"variants": {"a": {"name": "A", "profile_overrides": {"multi_did_max": 0}}}}
        assert schema_errors(EDITOR_SCHEMAS["variants"], doc) == [
            "variants.a.profile_overrides.multi_did_max: must be at least 1 (got 0)"
        ]
