"""Pin signals_schema.yaml to the code that writes and decodes broadcast signals.

The schema is the structural check, but the editor writes fields in the order of
``SIGNAL_FIELDS``/``MESSAGE_FIELDS`` and the decoder knows its own types, so the two must agree.
"""

from __future__ import annotations

from canair.core.json_schema import load_schema_file
from canair.core.paths import SIGNALS_SCHEMA_FILE
from canair.expression.decode_value import STRUCT, VALID_TYPES
from canair.validation import signals as v

SCHEMA = load_schema_file(SIGNALS_SCHEMA_FILE)
MESSAGE = SCHEMA["definitions"]["message"]
SIGNAL = SCHEMA["definitions"]["signal"]


def test_signal_fields_match():
    assert tuple(SIGNAL["properties"]) == v.SIGNAL_FIELDS
    assert SIGNAL["required"] == ["start_bit", "length"]


def test_message_fields_match():
    assert tuple(MESSAGE["properties"]) == v.MESSAGE_FIELDS


def test_enumerations_match():
    assert tuple(SIGNAL["properties"]["byte_order"]["enum"]) == v.BYTE_ORDERS
    # A broadcast signal is one field of a frame, so it has no struct type.
    types = tuple(t for t in VALID_TYPES if t != STRUCT)
    assert tuple(SIGNAL["properties"]["type"]["enum"]) == types
    lengths = [n for n in range(v.MAX_MESSAGE_LENGTH + 1) if v.is_message_length(n)]
    assert MESSAGE["properties"]["length"]["enum"] == lengths
