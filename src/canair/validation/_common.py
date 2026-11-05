"""Shared constants and helpers for the validate submodules."""

import re
from typing import Any

from canair.core import yaml_io
from canair.core.paths import SCHEMA_DIR

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


def is_int(value: Any) -> bool:
    """An int that is not a bool (``bool`` is an ``int`` subclass, so ``True`` would pass as 1)."""
    return isinstance(value, int) and not isinstance(value, bool)


def validate_state_list(value, label: str, field: str, errors: list, allowed: set) -> None:
    """Validate a ``vehicle_states``-style token list against ``allowed``.

    Tokens are compared case-insensitively (the vocabulary is canonically
    UPPERCASE), so a lower-case hold-over still validates while the tree
    migrates toward the uppercase form.
    """
    if value is None:
        return
    if not isinstance(value, list):
        errors.append(f"{label}: {field} must be a list")
        return
    from canair.states.tokens import unquoted_boolean_error

    if msg := unquoted_boolean_error(f"{label}: {field}", value):
        errors.append(msg)
        value = [v for v in value if not isinstance(v, bool)]
    allowed_upper = {str(a).upper() for a in allowed}
    for v in value:
        if str(v).upper() not in allowed_upper:
            errors.append(f"{label}: invalid {field} value '{v}' (allowed: {sorted(allowed)})")
    if len(value) != len(set(value)):
        errors.append(f"{label}: duplicate {field} values")


def validate_shared_fields(node: dict, label: str, errors: list) -> None:
    """Type-check the fields a PID can share with its parameters (on the PID or a parameter)."""
    for field in ("unit", "source"):
        value = node.get(field)
        if value is not None and not isinstance(value, str):
            errors.append(f"{label}: {field} must be a string")
    for field in ("min", "max"):
        value = node.get(field)
        if value is not None and (
            isinstance(value, bool) or not isinstance(value, (str, int, float))
        ):
            errors.append(f"{label}: {field} must be a number or a numeric string")
    verified = node.get("verified")
    if verified is not None and not isinstance(verified, bool):
        errors.append(f"{label}: verified must be a boolean")
    links = node.get("source_links")
    if links is not None and (
        not isinstance(links, list) or not all(isinstance(u, str) for u in links)
    ):
        errors.append(f"{label}: source_links must be a list of strings")
