"""Pin the human-readable signals_schema.yaml to the validator's authoritative field lists.

The schema file is documentation, so nothing reads it at runtime; without this test its
field lists and comment block drift from canlib/validation/signals.py silently.
"""

from __future__ import annotations

import re
from pathlib import Path

import yaml

from canlib.validation import signals as v

SCHEMA = Path(v.__file__).resolve().parent.parent / "schema" / "signals_schema.yaml"


def _schema() -> dict:
    return yaml.safe_load(SCHEMA.read_text())


def _comment_block_fields(indent: int) -> list[str]:
    """Field names documented in the header comment block at one indentation."""
    pattern = re.compile(rf"^#{' ' * indent}([a-z_]+):")
    return [m.group(1) for line in SCHEMA.read_text().splitlines() if (m := pattern.match(line))]


def test_signal_fields_match():
    s = _schema()
    assert (*s["required_signal_fields"], *s["optional_signal_fields"]) == v.SIGNAL_FIELDS


def test_message_fields_match():
    assert tuple(_schema()["optional_message_fields"]) == v.MESSAGE_FIELDS


def test_enumerations_match():
    s = _schema()
    assert tuple(s["valid_signal_types"]) == v.SIGNAL_TYPES
    assert tuple(s["byte_order_values"]) == v.BYTE_ORDERS
    assert tuple(s["float_signal_lengths"]) == v.FLOAT_LENGTHS
    assert [n for n in range(v.MAX_MESSAGE_LENGTH + 1) if v.is_message_length(n)] == s[
        "valid_message_lengths"
    ]


def test_comment_block_documents_every_field_in_order():
    assert tuple(_comment_block_fields(11)) == v.SIGNAL_FIELDS
    assert tuple(_comment_block_fields(7)) == v.MESSAGE_FIELDS
