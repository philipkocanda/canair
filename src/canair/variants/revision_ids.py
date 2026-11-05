"""How a revision ID is written, compared and checked.

An ID keeps the case it is written in (``UDS``). Comparing goes through
:func:`revision_key`, so a typed ``uds`` finds the declared ``UDS``.
"""

from __future__ import annotations

from collections.abc import Iterable

from canair.variants import id_problem

__all__ = [
    "canonical_revision",
    "normalize_revision_id",
    "revision_id_problem",
    "revision_key",
    "same_revision",
]


def normalize_revision_id(value: object) -> str:
    """A revision ID as written: stripped, case kept (``UDS`` stays ``UDS``)."""
    return str(value).strip()


def revision_key(value: object) -> str:
    """The form revision IDs are compared in: a typed ``uds`` finds the declared ``UDS``."""
    return str(value).strip().casefold()


def same_revision(a: object, b: object) -> bool:
    return revision_key(a) == revision_key(b)


def canonical_revision(value: object, revs: Iterable[str]) -> str:
    """``value`` spelled as the matching ID in ``revs``, or as typed when none matches."""
    wanted = revision_key(value)
    return next((r for r in revs if revision_key(r) == wanted), normalize_revision_id(value))


def revision_id_problem(rid: str) -> str | None:
    return id_problem(rid, "revision", "UDS")
