"""Variant tags on capture sessions: which car a recording came from.

A capture is evidence about one car. In a profile with several variants, a session
says which one with ``variant`` (a ``variants.yaml`` ID); for each captured ECU that
declares several revisions it says which with ``revisions: {ECU: id}``; and it records
how each was decided in ``attribution``. This module holds the rules every writer and
reader of those tags shares:

* :func:`capture_tag` — the tag a new recording gets: the profile's explicitly
  selected variant and its determined revisions, or nothing (canair never guesses);
* :func:`in_scope` — whether a session belongs to a ``--variant`` scope
  (an ID, ``all`` or ``untagged``), and :func:`revisions_in_scope` — whether it
  agrees with the revisions the selected car's definitions were resolved for;
* :func:`is_untagged_twin` — whether one copy of a session is another *minus its
  tags*, so merges and layered reads keep the tagged copy instead of letting a stale,
  untagged copy strip the tag.

It knows nothing about capture files or profiles' storage; callers hand it sessions
and a :class:`~canlib.profile.Profile`.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from canlib.profile import Profile

# ``--variant`` values that scope analysis rather than naming a car.
SCOPE_ALL = "all"
SCOPE_UNTAGGED = "untagged"
SCOPES = (SCOPE_ALL, SCOPE_UNTAGGED)

# The session fields a tag occupies, in on-disk order.
TAG_FIELDS = ("variant", "revisions", "attribution")


def selected_variant(profile: Profile) -> str | None:
    """The car the profile's ``--variant`` names, or None for none / a scope keyword."""
    variant = getattr(profile, "variant", None)
    return None if not variant or variant in SCOPES else variant


# How each way of selecting a variant is recorded on the session it tags.
_ATTRIBUTION = {"flag": "selected", "env": "selected", "config": "selected", "alias": "alias"}
# How each way of resolving a revision is recorded.
_REVISION_ATTRIBUTION = {"flag": "pinned", "config": "pinned", "only": "only"}


def _revision_tags(profile: Profile) -> tuple[dict[str, str], dict[str, str]]:
    """``({ECU: revision}, {ECU: attribution})`` for every determined multi-revision ECU."""
    from canlib.pids import load_raw_pids, revision_resolutions
    from canlib.variants.revisions import declared

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
    :func:`narrow_to_captured` keeps only those a session actually captured.
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


def narrow_to_captured(tag: Mapping[str, Any], captured_ecus: set[str]) -> dict[str, Any]:
    """``tag`` with revisions only for the ECUs in ``captured_ecus`` (upper-cased names)."""
    revisions = {
        e: r for e, r in (tag.get("revisions") or {}).items() if e.upper() in captured_ecus
    }
    out = {k: v for k, v in tag.items() if k != "revisions"}
    attribution = dict(out.get("attribution") or {})
    if revisions:
        out = {**out, "revisions": revisions}
        attribution["revisions"] = {
            e: s for e, s in (attribution.get("revisions") or {}).items() if e in revisions
        }
    else:
        attribution.pop("revisions", None)
    # Keep the on-disk order: variant, revisions, attribution.
    ordered = {k: out[k] for k in ("variant", "revisions") if k in out}
    if attribution:
        ordered["attribution"] = attribution
    return ordered


def session_variant(session: Mapping[Any, Any]) -> str | None:
    value = session.get("variant")
    return str(value) if value else None


def session_revisions(session: Mapping[Any, Any]) -> dict[str, str]:
    """The session's ``{ECU: revision}`` tags ({} when it carries none)."""
    value = session.get("revisions")
    return {str(k): str(v) for k, v in value.items()} if isinstance(value, Mapping) else {}


def revisions_in_scope(session: Mapping[Any, Any], resolved: Mapping[str, str]) -> bool:
    """Whether ``session`` agrees with the revisions the definitions were resolved for.

    A session tagged with another revision of a determined ECU is left out: decoding
    it with this revision's definitions would misread it. A session that doesn't say
    (no tag for that ECU) is read as the resolved revision, as an untagged session is
    read as the selected car.
    """
    if not resolved:
        return True
    tags = {k.upper(): v for k, v in session_revisions(session).items()}
    return all(tags.get(ecu.upper(), rid) == rid for ecu, rid in resolved.items())


def in_scope(session: Mapping[Any, Any], scope: str | None, *, several: bool = False) -> bool:
    """Whether ``session`` belongs to the analysis ``scope``.

    ``scope`` is the selected ``--variant``: an ID (that car's sessions, plus the untagged
    ones, read as that car), ``all``, ``untagged``, or None. ``several`` says the profile
    declares more than one variant; only then does None leave the untagged sessions out —
    with no car named, nothing says which car they are. With one variant or none there is
    nothing to tell apart, and every session is in scope.
    """
    if scope == SCOPE_ALL:
        return True
    variant = session_variant(session)
    if scope == SCOPE_UNTAGGED:
        return variant is None
    if variant is None:
        return bool(scope) or not several
    return not scope or variant == scope


def strip_tag(session: Mapping[Any, Any]) -> dict[Any, Any]:
    """``session`` without its tag fields."""
    return {k: v for k, v in session.items() if k not in TAG_FIELDS}


def is_untagged_twin(untagged: Mapping[Any, Any], tagged: Mapping[Any, Any]) -> bool:
    """``untagged`` is ``tagged`` minus its tag: the same session, with less information."""
    return (
        session_variant(untagged) is None
        and session_variant(tagged) is not None
        and dict(untagged) == strip_tag(tagged)
    )


def revision_spread(
    entries: Iterable[Mapping[str, Any]], ecus: Iterable[str] | None = None
) -> dict[str, list[str]]:
    """ECU -> the distinct revisions its captures in ``entries`` are tagged with.

    ``entries`` are :func:`canlib.captures.store.load_all_captures` rows (or slimmer rows
    carrying ``ecu`` and ``session_revisions``); ``ecus`` narrows to those ECUs.
    """
    wanted = {e.upper() for e in ecus} if ecus is not None else None
    seen: dict[str, list[str]] = {}
    for e in entries:
        ecu = str(e.get("ecu", "")).upper()
        if wanted is not None and ecu not in wanted:
            continue
        tags = {k.upper(): v for k, v in (e.get("session_revisions") or {}).items()}
        rid = tags.get(ecu)
        if rid and rid not in seen.setdefault(ecu, []):
            seen[ecu].append(rid)
    return {ecu: revs for ecu, revs in seen.items() if revs}


def require_one_revision(
    entries: Iterable[Mapping[str, Any]], ecus: Iterable[str] | None, what: str
) -> None:
    """Refuse a byte-level analysis whose scope spans two revisions of an analysed ECU.

    A byte offset means nothing across two layouts, so ``investigate``, ``hunt``,
    ``correlate``, ``coverage`` and ``decode --dump-bytes``/``--try`` call this before
    reading bytes. Raises :class:`~canlib.profile.ProfileError` naming the fix.
    """
    from canlib.profile import ProfileError

    spread = {e: r for e, r in revision_spread(entries, ecus).items() if len(r) > 1}
    if not spread:
        return
    ecu, revs = next(iter(spread.items()))
    raise ProfileError(
        f"{what}: the captures in scope span {ecu} revisions {', '.join(revs)}, and a byte "
        f"offset means nothing across two layouts. Scope to one with "
        f"`canair --revision {ecu}={revs[0]} …` (or a --variant that allows only one)."
    )
