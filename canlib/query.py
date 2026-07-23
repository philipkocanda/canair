"""ECU / PID selection mini-language.

A small, source-agnostic query syntax for picking ECUs and PIDs on the command
line. Shared by the capture tools (``canair captures``) and intended for reuse
anywhere an ECU/PID selection is needed (e.g. ``canair query``).

Grammar
-------
::

    QUERY    := SELECTOR (WHITESPACE SELECTOR)*
    SELECTOR := ECU [ ':' PIDLIST ]
    PIDLIST  := PID (',' PID)*

- Whitespace separates independent selectors (logical OR across selectors).
- A selector is an ECU name, optionally followed by ``:`` and a comma-separated
  PID list. With no PID list, the selector matches *all* PIDs for that ECU.
- Matching is case-insensitive. Each PID token matches a capture's PID by exact
  match *or* substring (so ``22`` matches every ``22xxxx`` DID).

Examples
--------
=========================  ==================================================
``VCU``                    all PIDs for VCU
``VCU:2101``               VCU PID 2101 only
``VCU:2101,22BC03``        VCU PIDs 2101 and 22BC03
``VCU:22``                 all VCU DIDs whose PID contains "22"
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

__all__ = ["Query", "QueryError", "Selector", "parse_query", "parse_selector"]

T = TypeVar("T")


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
        """True if ``pid`` matches any token (exact or substring), or ALL."""
        if not self.pids:
            return True
        p = str(pid).upper()
        return any(tok == p or tok in p for tok in self.pids)

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
