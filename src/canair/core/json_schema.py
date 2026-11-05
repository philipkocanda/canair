"""Checking a parsed document against one of canair's JSON Schemas, with readable findings.

:func:`schema_errors` returns one line per problem and never raises. A line reads
``path: message``: the path is dotted with ``[i]`` for a list index
(``messages.0x220.signals.GEAR.length``), and the message is phrased for a person editing YAML
(``unknown field 'x' (allowed: a, b)``, ``must be an integer (got 'abc')``).

A subschema can replace the message with ``errorMessage`` (any failure in that subschema) or
``patternErrorMessage`` (a failed ``pattern``). VS Code and yaml-language-server show the same two
keywords, so canair and an editor say the same thing.

The schemas refer to each other by ``$id``, the URL the docs site publishes them at, so a
``$ref`` to ``profile.json#/properties/quirks`` resolves here as it does in an editor.
"""

from __future__ import annotations

import json
import re
from collections.abc import Iterable, Sequence
from functools import cache
from pathlib import Path
from typing import TYPE_CHECKING, Any

from canair.core import yaml_io
from canair.core.paths import SCHEMA_DIR

if TYPE_CHECKING:
    from jsonschema.exceptions import ValidationError
    from jsonschema.protocols import Validator

_TYPE_NAMES = {
    "string": "text",
    "integer": "an integer",
    "number": "a number",
    "boolean": "true or false",
    "object": "a mapping",
    "array": "a list",
    "null": "empty",
}
_BOOLEAN_HINT = "YAML reads an unquoted ON, OFF, YES or NO as true or false, so quote it"
_PLAIN_KEY = re.compile(r"[^\s.\[\]'\"]+")
_REQUIRED = re.compile(r"^(.*) is a required property$")


def load_schema_file(path: Path) -> dict:
    """A schema file's contents: JSON for ``.json``, YAML otherwise."""
    text = path.read_text()
    return json.loads(text) if path.suffix == ".json" else yaml_io.safe_load(text)


def schema_errors(path: Path, data: Any) -> list[str]:
    """Every problem in ``data`` against the schema at ``path``, in document order."""
    validator = _validator(path)
    found = sorted(validator.iter_errors(_text_keys(data)), key=_sort_key)
    out: list[str] = []
    for error in found:
        for line in _lines(error):
            if line not in out:
                out.append(line)
    return out


def check_schema_file(path: Path) -> None:
    """Raise :class:`jsonschema.exceptions.SchemaError` unless ``path`` is a valid schema."""
    from jsonschema.validators import validator_for

    schema = load_schema_file(path)
    validator_for(schema).check_schema(schema)


@cache
def _registry():
    from referencing import Registry, Resource

    resources = []
    for path in sorted(SCHEMA_DIR.iterdir()):
        if path.suffix not in (".json", ".yaml"):
            continue
        doc = load_schema_file(path)
        if isinstance(doc, dict) and isinstance(doc.get("$id"), str) and "$schema" in doc:
            resources.append((doc["$id"], Resource.from_contents(doc)))
    return Registry().with_resources(resources)


@cache
def _validator(path: Path) -> Validator:
    from jsonschema.validators import validator_for

    schema = load_schema_file(path)
    cls = validator_for(schema)
    cls.check_schema(schema)
    return cls(schema, registry=_registry())


def _text_keys(data: Any) -> Any:
    """``data`` with every mapping key as text: JSON Schema knows only string keys."""
    if isinstance(data, dict):
        return {_key_text(k): _text_keys(v) for k, v in data.items()}
    if isinstance(data, list | tuple):
        return [_text_keys(v) for v in data]
    return data


def _key_text(key: Any) -> str:
    if isinstance(key, bool):
        return "true" if key else "false"
    return str(key)


def _sort_key(error: ValidationError) -> tuple:
    return tuple((0, p, "") if isinstance(p, int) else (1, 0, str(p)) for p in error.absolute_path)


def _where(path: Iterable[Any]) -> str:
    out = ""
    for part in path:
        if isinstance(part, int):
            out += f"[{part}]"
        else:
            text = str(part) if _PLAIN_KEY.fullmatch(str(part)) else repr(str(part))
            out += f".{text}" if out else text
    return out


def _lines(error: ValidationError) -> list[str]:
    from jsonschema.exceptions import best_match

    schema = error.schema if isinstance(error.schema, dict) else {}
    if error.validator in ("anyOf", "oneOf") and error.context and "errorMessage" not in schema:
        return _lines(best_match(error.context))
    path = list(error.absolute_path)
    if "propertyNames" in error.absolute_schema_path:
        path.append(error.instance)
    where = _where(path)
    return [f"{where}: {m}" if where else m for m in _messages(error, schema)]


def _messages(error: ValidationError, schema: dict) -> list[str]:
    keyword, value, instance = error.validator, error.validator_value, error.instance
    if keyword == "pattern" and isinstance(schema.get("patternErrorMessage"), str):
        return [schema["patternErrorMessage"]]
    if isinstance(schema.get("errorMessage"), str):
        return [schema["errorMessage"]]
    got = f" (got {_show(instance)})"
    if keyword == "type":
        return [_type_message(value, instance)]
    if keyword == "enum":
        return [f"must be one of {', '.join(_show(v) for v in value)}{got}"]
    if keyword == "const":
        return [f"must be {_show(value)}{got}"]
    if keyword == "required":
        match = _REQUIRED.match(error.message)
        return [f"missing required field {match.group(1) if match else error.message}"]
    if keyword == "additionalProperties" and value is False and isinstance(instance, dict):
        return _unknown_fields(schema, instance)
    bounds = {
        "minimum": "at least",
        "maximum": "at most",
        "exclusiveMinimum": "greater than",
        "exclusiveMaximum": "less than",
    }
    if keyword in bounds:
        return [f"must be {bounds[keyword]} {value}{got}"]
    if keyword in ("minItems", "maxItems"):
        if keyword == "minItems" and value == 1:
            return ["must not be empty"]
        if schema.get("minItems") == schema.get("maxItems"):
            return [f"must hold exactly {value} items"]
        return [f"must hold {'at least' if keyword == 'minItems' else 'at most'} {value} items"]
    if keyword == "minLength":
        return ["must not be empty" if value == 1 else f"must be at least {value} characters"]
    if keyword == "pattern":
        return [f"must match {value}{got}"]
    if keyword == "uniqueItems":
        return ["must not list an item twice"]
    return [error.message]


def _unknown_fields(schema: dict, instance: dict) -> list[str]:
    props = schema.get("properties") or {}
    patterns = schema.get("patternProperties") or {}
    allowed = f" (allowed: {', '.join(props)})" if props else ""
    return [
        f"unknown field '{key}'{allowed}"
        for key in instance
        if key not in props and not any(re.search(p, key) for p in patterns)
    ]


def _type_message(expected: str | Sequence[str], instance: Any) -> str:
    types = [expected] if isinstance(expected, str) else list(expected)
    message = f"must be {' or '.join(_TYPE_NAMES.get(t, t) for t in types)}"
    if isinstance(instance, bool) and "string" in types:
        return f"{message} (got {_show(instance)}: {_BOOLEAN_HINT})"
    return f"{message} (got {_show(instance)})"


def _show(value: Any) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    if value is None:
        return "nothing"
    if isinstance(value, dict):
        return "a mapping"
    if isinstance(value, list):
        return "a list"
    if isinstance(value, str):
        return repr(value)
    return str(value)
