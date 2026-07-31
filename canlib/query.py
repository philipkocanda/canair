"""ECU / PID selection mini-language.

A small, source-agnostic query syntax for picking ECUs and PIDs on the command
line. Shared by the capture tools (``canair captures``) and intended for reuse
anywhere an ECU/PID selection is needed (e.g. ``canair read``).

Grammar
-------
::

    QUERY    := SELECTOR (WHITESPACE SELECTOR)*
    SELECTOR := ECU [ ':' PIDLIST ]
    PIDLIST  := PID (',' PID)*

- Whitespace separates independent selectors (logical OR across selectors).
- A selector is an ECU name, optionally followed by ``:`` and a comma-separated
  PID list. With no PID list, the selector matches *all* PIDs for that ECU.
- Matching is case-insensitive. Each PID token matches a capture's PID by a
  boundary-anchored match: the PID must *start with* or *end with* the token
  (so ``22`` matches every ``22xxxx`` service DID and ``BC03`` matches the
  stored ``22BC03`` regardless of service byte). A token that appears only in
  the middle does not match.

Examples
--------
=========================  ==================================================
``VCU``                    all PIDs for VCU
``VCU:2101``               VCU PID 2101 only
``VCU:2101,22BC03``        VCU PIDs 2101 and 22BC03
``VCU:22``                 all VCU DIDs whose PID starts with "22"
``IGPM:BC03``              the ``22BC03`` DID (suffix match, service-byte free)
``VCU:2101 BMS:2101``      VCU 2101 and BMS 2101 (cross-ECU)
``BMS``                    all PIDs for BMS
=========================  ==================================================

Usage
-----
::

    from canlib.query import parse_query

    query = parse_query("VCU:2101,2102 BMS")          # or a list of tokens
    matched, empty = query.filter(
        records, ecu_of=lambda r: r["ecu"], pid_of=lambda r: r["pid"]
    )
    # `matched` = records matching any selector (input order preserved)
    # `empty`   = selectors that matched nothing (for diagnostics)
"""

from __future__ import annotations

from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass
from typing import TypeVar

__all__ = ["Query", "QueryError", "Selector", "looks_like_pid", "parse_query", "parse_selector"]

T = TypeVar("T")

_HEX_DIGITS = frozenset("0123456789ABCDEF")


def looks_like_pid(token: str) -> bool:
    """True if ``token`` looks like a bare PID/DID rather than an ECU name.

    Real ECU names are alphabetic (IGPM, BMS, VCU, …); PIDs/DIDs are hex tokens
    that contain a digit (2101, 22BC07, BC03, C00B, B00E). A bare hex-with-digit
    token in the ``ECU`` position is almost always a PID accidentally separated
    from its ECU by a space instead of a colon. The single source of truth for
    the space-vs-colon guard shared by the query-step parser and the capture
    query hint.
    """
    t = token.upper()
    return len(t) >= 2 and all(c in _HEX_DIGITS for c in t) and any(c.isdigit() for c in t)


class QueryError(ValueError):
    """Raised when a query string is malformed."""


@dataclass(frozen=True)
class Selector:
    """One ``ECU[:PIDLIST]`` clause.

    Attributes:
        ecu:  ECU name, upper-cased.
        pids: PID tokens, upper-cased. Empty tuple means "all PIDs".
    """

    ecu: str
    pids: tuple[str, ...] = ()

    def matches_ecu(self, ecu: str) -> bool:
        return str(ecu).upper() == self.ecu

    def matches_pid(self, pid: str) -> bool:
        """True if ``pid`` matches any token (prefix or suffix), or ALL.

        A token matches when the PID *starts with* or *ends with* it. This
        anchors matching to a boundary so ``22`` picks the ``22xxxx`` service-22
        DIDs (prefix) and ``BC03`` picks ``22BC03`` regardless of service byte
        (suffix), without a token matching arbitrarily in the middle.
        """
        if not self.pids:
            return True
        p = str(pid).upper()
        return any(p == tok or p.startswith(tok) or p.endswith(tok) for tok in self.pids)

    def matches(self, ecu: str, pid: str) -> bool:
        return self.matches_ecu(ecu) and self.matches_pid(pid)

    def __str__(self) -> str:
        return self.ecu + (":" + ",".join(self.pids) if self.pids else "")


@dataclass(frozen=True)
class Query:
    """A parsed query: an OR of one or more :class:`Selector` clauses."""

    selectors: tuple[Selector, ...]

    def matches(self, ecu: str, pid: str) -> bool:
        """True if any selector matches ``(ecu, pid)``."""
        return any(s.matches(ecu, pid) for s in self.selectors)

    def canonicalize_ecus(self, resolver: Callable[[str], str]) -> Query:
        """Return a copy with each selector's ECU mapped through ``resolver``.

        ``resolver`` maps a selector ECU token (already upper-cased) to a
        canonical, upper-cased ECU name — e.g. resolving an alias to the
        module's primary name. Source-agnostic: the caller supplies the mapping
        (see :func:`canlib.ecus.canonical_ecu_name`).
        """
        return Query(tuple(Selector(resolver(s.ecu), s.pids) for s in self.selectors))

    def filter(
        self,
        records: Iterable[T],
        *,
        ecu_of: Callable[[T], str],
        pid_of: Callable[[T], str],
    ) -> tuple[list[T], list[Selector]]:
        """Filter ``records`` to those matching any selector.

        Args:
            records: Iterable of arbitrary records.
            ecu_of:  Callable extracting the ECU name from a record.
            pid_of:  Callable extracting the PID string from a record.

        Returns:
            ``(matched, empty)`` where ``matched`` preserves input order and
            ``empty`` lists the selectors that matched no record (useful for
            "you asked for X but nothing matched" diagnostics).
        """
        matched: list[T] = []
        used = [False] * len(self.selectors)
        for rec in records:
            ecu = ecu_of(rec)
            pid = pid_of(rec)
            hit = False
            for idx, sel in enumerate(self.selectors):
                if sel.matches(ecu, pid):
                    used[idx] = True
                    hit = True
            if hit:
                matched.append(rec)
        empty = [sel for sel, ok in zip(self.selectors, used, strict=True) if not ok]
        return matched, empty

    def __str__(self) -> str:
        return " ".join(str(s) for s in self.selectors)


def parse_selector(token: str) -> Selector:
    """Parse a single ``ECU[:PIDLIST]`` token into a :class:`Selector`."""
    ecu_part, sep, pid_part = token.partition(":")
    ecu = ecu_part.strip().upper()
    if not ecu:
        raise QueryError(f"selector {token!r} has an empty ECU")

    if not sep:
        return Selector(ecu, ())

    if ":" in pid_part:
        raise QueryError(f"selector {token!r} has more than one ':'")

    pids = tuple(p.strip().upper() for p in pid_part.split(",") if p.strip())
    return Selector(ecu, pids)


def parse_query(query: str | Sequence[str]) -> Query:
    """Parse a query string (or list of tokens) into a :class:`Query`.

    Accepts either a raw string (``"VCU:2101 BMS"``) or a pre-split token list
    from argparse ``nargs="+"`` (``["VCU:2101", "BMS"]``), which is joined with
    spaces before parsing.

    Raises:
        QueryError: if the query is empty or any selector is malformed.
    """
    if not isinstance(query, str):
        query = " ".join(query)

    tokens = query.split()
    if not tokens:
        raise QueryError("empty query")

    return Query(tuple(parse_selector(tok) for tok in tokens))
