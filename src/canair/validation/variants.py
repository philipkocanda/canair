"""The check behind ``validate variants``: the profile's optional ``variants.yaml``."""

from __future__ import annotations

from canair.core import yaml_io

from ._common import duplicate_key_errors
from .report import FileReport


def _reference_errors() -> list[str]:
    """Every ECU or revision a variant's ``revisions:`` names that ecus/ lacks."""
    from canair.ecus.pids import load_raw_pids
    from canair.variants import load_variants
    from canair.variants.revisions import variant_reference_errors

    try:
        ecus = load_raw_pids().get("ecus") or {}
    except Exception:
        return []  # a broken ecus/ is `validate pids`'s to report
    return variant_reference_errors(load_variants(), ecus)


def check_variants() -> FileReport:
    """Check ``variants.yaml``: structure, IDs, every metadata field, and that every ECU
    revision it names exists."""
    from canair.profile import active
    from canair.variants import check_variants_doc

    path = active().variants_file
    if not path.exists():
        return FileReport(path.name, skipped="No variants.yaml (optional) — skipping.")
    try:
        text = path.read_text()
        data = yaml_io.safe_load(text)
    except Exception as e:
        return FileReport(path.name, broken=f"{path.name}: YAML parse error: {e}")
    errors = duplicate_key_errors(text) + check_variants_doc(data)
    if not errors:
        errors = _reference_errors()
    if errors:
        return FileReport(path.name, errors=tuple(errors))
    return FileReport(path.name, summary=f"({len((data or {}).get('variants') or {})} variant(s))")
