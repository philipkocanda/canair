"""Shared YAML loading helpers.

canair reads a lot of YAML on every command (per-ECU definitions, capture
files, schemas, config, profile settings). The default ``yaml.safe_load`` uses
the pure-Python parser, which is 5-10x slower than the libyaml-backed C loader.
On a mature profile with thousands of captures that pure-Python parse dominates
the wall-clock time of read-only commands (e.g. ``canair ecu bms``).

This module centralises the "prefer the C loader" choice so every reader gets
the speedup without duplicating the fallback dance, and so there is a single
place to change loading behaviour. It is behaviourally identical to
``yaml.safe_load`` — same accepted YAML, same Python objects — just faster when
libyaml is present. :func:`duplicate_keys` is the one check the loaders cannot
make: a repeated mapping key, which they silently resolve to the last value.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import IO, Any

try:
    import yaml
except ImportError as e:  # pragma: no cover - environment-specific
    raise ImportError("PyYAML not installed. Run: pip3 install pyyaml") from e

# Prefer the libyaml-backed C loader when available (5-10x faster parse, no
# behavioural difference). Falls back to the pure-Python SafeLoader otherwise.
SafeLoader = getattr(yaml, "CSafeLoader", yaml.SafeLoader)


def safe_load(stream: str | bytes | IO[str] | IO[bytes]) -> Any:
    """Drop-in replacement for :func:`yaml.safe_load` using the C loader.

    Accepts a string/bytes document or an open text/binary file object, exactly
    like ``yaml.safe_load``.
    """
    return yaml.load(stream, Loader=SafeLoader)


def load_path(path: Path | str) -> Any:
    """Load and parse a YAML file by path (read as text, C loader)."""
    return yaml.load(Path(path).read_text(), Loader=SafeLoader)


_MERGE_TAG = "tag:yaml.org,2002:merge"


@dataclass(frozen=True)
class DuplicateKey:
    """A mapping key that appears twice; loading keeps only the later value."""

    path: tuple[str, ...]  # keys (and ``[i]`` sequence indexes) leading to the mapping
    key: str
    line: int  # 1-based line of the repeated key
    first_line: int  # 1-based line of its first occurrence


def duplicate_keys(text: str) -> list[DuplicateKey]:
    """Every repeated key in any mapping of a YAML document, in document order.

    PyYAML (like ruamel's round-trip mode) silently keeps the last value of a repeated
    key, so a hand edit or a merge that writes one twice loads without complaint.
    Keys compare by their written text, so ``2101`` and ``"2101"`` count as the same
    key (canair normalises PID codes to text). Raises :class:`yaml.YAMLError` on a
    document that does not parse.
    """
    found: list[DuplicateKey] = []
    visited: set[int] = set()

    def walk(node: yaml.Node | None, path: tuple[str, ...]) -> None:
        if node is None or id(node) in visited:  # an alias already walked at its anchor
            return
        visited.add(id(node))
        if isinstance(node, yaml.MappingNode):
            first: dict[str, int] = {}
            for key_node, value_node in node.value:
                if isinstance(key_node, yaml.ScalarNode) and key_node.tag != _MERGE_TAG:
                    key, line = key_node.value, key_node.start_mark.line + 1
                    if key in first:
                        found.append(DuplicateKey(path, key, line, first[key]))
                    else:
                        first[key] = line
                    walk(value_node, (*path, key))
                else:
                    walk(value_node, path)
        elif isinstance(node, yaml.SequenceNode):
            for i, item in enumerate(node.value):
                walk(item, (*path, f"[{i}]"))

    walk(yaml.compose(text, Loader=SafeLoader), ())
    return found
