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
libyaml is present.
"""

from __future__ import annotations

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
