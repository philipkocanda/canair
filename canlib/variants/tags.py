"""Variant tags on capture sessions: which car a recording came from.

A capture is evidence about one car. In a profile with several variants, a session
says which one with ``variant`` (a ``variants.yaml`` ID) and records how that was
decided in ``attribution``. This module holds the rules every writer and reader of
those tags shares:

* :func:`capture_tag` — the tag a new recording gets: the profile's explicitly
  selected variant, or nothing (canair never guesses a car);
* :func:`in_scope` — whether a session belongs to a ``--variant`` scope
  (an ID, ``all`` or ``untagged``);
* :func:`is_untagged_twin` — whether one copy of a session is another *minus its
  tags*, so merges and layered reads keep the tagged copy instead of letting a stale,
  untagged copy strip the tag.

It knows nothing about capture files or profiles' storage; callers hand it sessions
and a :class:`~canlib.profile.Profile`.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from canlib.profile import Profile

# ``--variant`` values that scope analysis rather than naming a car.
SCOPE_ALL = "all"
SCOPE_UNTAGGED = "untagged"
SCOPES = (SCOPE_ALL, SCOPE_UNTAGGED)

# The session fields a tag occupies.
TAG_FIELDS = ("variant", "attribution")


def selected_variant(profile: Profile) -> str | None:
    """The car the profile's ``--variant`` names, or None for none / a scope keyword."""
    variant = getattr(profile, "variant", None)
    return None if not variant or variant in SCOPES else variant


def capture_tag(profile: Profile) -> dict[str, Any] | None:
    """The tag a new recording against ``profile`` gets, or None (left untagged)."""
    variant = selected_variant(profile)
    if variant is None:
        return None
    return {"variant": variant, "attribution": {"variant": "selected"}}


def session_variant(session: Mapping[Any, Any]) -> str | None:
    value = session.get("variant")
    return str(value) if value else None


def in_scope(session: Mapping[Any, Any], scope: str | None) -> bool:
    """Whether ``session`` belongs to the ``--variant`` ``scope`` (None = everything)."""
    if not scope or scope == SCOPE_ALL:
        return True
    variant = session_variant(session)
    if scope == SCOPE_UNTAGGED:
        return variant is None
    return variant == scope


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
