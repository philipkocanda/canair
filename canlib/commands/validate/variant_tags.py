"""``validate captures``: which car each session is tagged with.

Two findings, both reported as a footer rather than per file:

* sessions tagged with a variant the profile does not declare — the tag names a
  car the profile no longer knows (a hand edit, or a ``variants.yaml`` change made
  without ``canair variants rename``);
* on a profile with more than one variant, how many sessions carry no tag at all,
  since analysis can't tell which car those came from.
"""

from __future__ import annotations

from collections import Counter
from pathlib import Path

from canlib.captures import io as capture_io


def variant_tag_findings(files: list[Path], declared: set[str]) -> tuple[int, Counter[str]]:
    """``(untagged sessions, undeclared tag -> sessions)`` across ``files``."""
    untagged = 0
    undeclared: Counter[str] = Counter()
    for path in files:
        for session in (capture_io.load_capture_file(path) or {}).get("sessions") or []:
            variant = session.get("variant")
            if not variant:
                untagged += 1
            elif variant not in declared:
                undeclared[str(variant)] += 1
    return untagged, undeclared


def report_variant_tags(files: list[Path], profile) -> int:
    """Print the variant-tag findings for ``files``; returns the number of warnings."""
    from canlib.variants import VariantsError, load_variants

    try:
        declared = set(load_variants(profile))
    except VariantsError:
        return 0  # `validate variants` reports a broken variants.yaml
    untagged, undeclared = variant_tag_findings(files, declared)
    for variant, n in sorted(undeclared.items()):
        known = ", ".join(sorted(declared)) or "none"
        print(
            f"  ⚠ {n} session(s) tagged with variant {variant!r}, which the profile does not "
            f"declare (declared: {known}) — retag with `captures uds --set-variant`"
        )
    if len(declared) > 1 and untagged:
        print(
            f"  ({untagged} untagged session(s): no car named — tag them with "
            "`captures migrate-variants` or `captures uds --set-variant`)"
        )
    return sum(undeclared.values())
