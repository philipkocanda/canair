"""``validate variants`` — the profile's optional ``variants.yaml``."""

from __future__ import annotations

from canlib import yaml_io


def _reference_errors() -> list[str]:
    """Every ECU or revision a variant's ``revisions:`` names that ecus/ lacks."""
    from canlib.pids import load_raw_pids
    from canlib.variants import load_variants
    from canlib.variants.revisions import variant_reference_errors

    try:
        ecus = load_raw_pids().get("ecus") or {}
    except Exception:
        return []  # a broken ecus/ is `validate pids`'s to report
    return variant_reference_errors(load_variants(), ecus)


def _run_variants() -> int:
    """Validate ``variants.yaml``: structure, IDs, every metadata field, and that every ECU
    revision it names exists."""
    from canlib.profile import active
    from canlib.variants import check_variants_doc

    path = active().variants_file
    if not path.exists():
        print("No variants.yaml (optional) — skipping.")
        return 0
    try:
        data = yaml_io.safe_load(path.read_text())
    except Exception as e:
        print(f"{path.name}: YAML parse error: {e}")
        return 1
    errors = check_variants_doc(data)
    if not errors:
        errors = _reference_errors()
    if errors:
        for e in errors:
            print(f"  ERROR: {path.name}: {e}")
        print(f"\n{len(errors)} error(s)")
        return 1
    count = len((data or {}).get("variants") or {})
    print(f"{path.name}: OK ({count} variant(s))")
    return 0
