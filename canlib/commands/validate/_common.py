"""Shared constants and helpers for the validate submodules."""

import re

from canlib import yaml_io
from canlib.constants import SCHEMA_DIR

SCHEMA_FILE = SCHEMA_DIR / "pids_schema.yaml"
CAPTURES_SCHEMA_FILE = SCHEMA_DIR / "captures_schema.json"
CAN_INDEX_SCHEMA_FILE = SCHEMA_DIR / "can_index_schema.json"
SIGNALS_SCHEMA_FILE = SCHEMA_DIR / "signals_schema.yaml"

# Regex for valid WiCAN expressions (basic sanity — not a full parser)
EXPR_TOKEN_RE = re.compile(
    r"\[[BS]\d+:[BS]\d+\]|"  # [Bnn:Bmm] multi-byte (must be before Bnn:k)
    r"[BS]\d+:\d+|"  # Bnn:k (bit access)
    r"[BS]\d+|"  # Bnn, Snn
    r"V|"  # external value
    r"[0-9]+\.?[0-9]*|"  # numeric literal
    r"[+\-*/()&|^<>=\s]"  # operators, whitespace
)

DEPRECATED_FIELDS = {"ecu_tx", "ecu_rx", "ecu_name", "decoded"}


def duplicate_key_errors(text: str) -> list[str]:
    """One error per key a YAML document repeats (the loaders keep the later value).

    Call it on text that has already parsed; it raises :class:`yaml.YAMLError` otherwise.
    """
    return [
        f"duplicate key '{d.key}' in {'/'.join(d.path) or 'the top level'} at line {d.line}"
        f" (first at line {d.first_line}); only the later one is loaded"
        for d in yaml_io.duplicate_keys(text)
    ]
