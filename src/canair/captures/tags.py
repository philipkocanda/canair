"""The variant tag a new recording gets: which car it came from.

The rules every reader of a session's tags shares (scopes, twins, spreads) are
:mod:`canair.variants.tags`. The tag itself needs the profile's definitions (which ECUs have
several revisions, and which one this car has), so it is built here.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from canair.ecus.pids import load_raw_pids, revision_resolutions
from canair.variants.revisions import declared
from canair.variants.tags import selected_variant

if TYPE_CHECKING:
    from canair.profile import Profile

# How each way of selecting a variant is recorded on the session it tags.
_ATTRIBUTION = {"flag": "selected", "env": "selected", "config": "selected", "alias": "alias"}
# How each way of resolving a revision is recorded.
_REVISION_ATTRIBUTION = {
    "flag": "pinned",
    "config": "pinned",
    "only": "only",
    "detected": "detected",
}


def _revision_tags(profile: Profile) -> tuple[dict[str, str], dict[str, str]]:
    """``({ECU: revision}, {ECU: attribution})`` for every determined multi-revision ECU."""
    try:
        resolutions = revision_resolutions(profile)
        ecus = load_raw_pids(profile).get("ecus") or {}
    except Exception:
        return {}, {}
    revisions: dict[str, str] = {}
    sources: dict[str, str] = {}
    for name, res in resolutions.items():
        if res.revision is None or len(declared(ecus.get(name))) < 2:
            continue
        revisions[name] = res.revision
        sources[name] = _REVISION_ATTRIBUTION.get(res.source or "only", "only")
    return revisions, sources


def capture_tag(profile: Profile) -> dict[str, Any] | None:
    """The tag a new recording against ``profile`` gets, or None (left untagged).

    The revisions cover every determined ECU with several revisions;
    :func:`canair.variants.tags.narrow_to_captured` keeps only those a session actually captured.
    """
    variant = selected_variant(profile)
    revisions, rev_sources = _revision_tags(profile)
    if variant is None and not revisions:
        return None
    tag: dict[str, Any] = {}
    attribution: dict[str, Any] = {}
    if variant is not None:
        tag["variant"] = variant
        source = getattr(profile, "variant_source", None)
        attribution["variant"] = _ATTRIBUTION.get(source, "only")
    if revisions:
        tag["revisions"] = revisions
        attribution["revisions"] = rev_sources
    tag["attribution"] = attribution
    return tag
