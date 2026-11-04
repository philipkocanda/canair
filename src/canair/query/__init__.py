"""ECU / PID selection mini-language.

A small, source-agnostic query syntax for picking ECUs and PIDs on the command
line. Shared by the capture tools (``canair captures``) and intended for reuse
anywhere an ECU/PID selection is needed (e.g. ``canair read``).

Belongs: the selector grammar and matching (this module) and the ``read``/``monitor`` step
grammar built on it (``steps``).
Not here: running a step against the car (``live/multi*``) or ``@group`` definitions (``ecus/``).
Imports: ``ecus`` (to resolve names) and ``core``.

Grammar
-------
::

    QUERY    := SELECTOR (WHITESPACE SELECTOR)*
    SELECTOR := ECU [ ':' PIDLIST ]
    PIDLIST  := PID (',' PID)*
    PID      := HEX | SERVICE '/' ID

- Whitespace separates independent selectors (logical OR across selectors).
- A selector is an ECU name, optionally followed by ``:`` and a comma-separated
  PID list. With no PID list, the selector matches *all* PIDs for that ECU.
- Matching is case-insensitive. Each PID token matches a capture's PID by a
  boundary-anchored match: the PID must *start with* or *end with* the token
  (so ``22`` matches every ``22xxxx`` service DID and ``BC03`` matches the
  stored ``22BC03`` regardless of service byte). A token that appears only in
  the middle does not match. A live read (:func:`pids_named_by`) picks defined
  PIDs by the same rule, except that a token equal to a defined PID picks only
  that one.
- A ``SERVICE/ID`` token names the service explicitly and matches exactly one
  request code: ``01/0D`` is OBD-II mode 01 PID 0D (request ``010D``), which a
  bare ``010D`` cannot say, since four hex digits read as a service-22 DID.
  :data:`EXPLICIT_SERVICES` lists the services it accepts.

Examples
--------
=========================  ==================================================
``VCU``                    all PIDs for VCU
``VCU:2101``               VCU PID 2101 only
``VCU:2101,22BC03``        VCU PIDs 2101 and 22BC03
``VCU:22``                 all VCU DIDs whose PID starts with "22"
``IGPM:BC03``              the ``22BC03`` DID (suffix match, service-byte free)
``BMS:01/0D``              OBD-II mode 01 PID 0D (exactly ``010D``)
``VCU:2101 BMS:2101``      VCU 2101 and BMS 2101 (cross-ECU)
``BMS``                    all PIDs for BMS
=========================  ==================================================

Usage
-----
::

    from canair.query import parse_query

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

__all__ = [
    "EXPLICIT_SERVICES",
    "Query",
    "QueryError",
    "Selector",
    "explicit_request",
    "inferred_request",
    "looks_like_pid",
    "parse_query",
    "parse_selector",
    "pids_named_by",
]

T = TypeVar("T")

_HEX_DIGITS = frozenset("0123456789ABCDEF")

#: The services a ``SERVICE/ID`` token may name, each with its identifier width in
#: bytes: OBD-II mode 01 (current data) and mode 09 (vehicle information), and the
#: two diagnostic reads the bare forms infer (KWP2000 ``21``, UDS ``22``). Only
#: single-identifier reads: a multi-PID mode 01 request is not a capture key.
EXPLICIT_SERVICES: dict[str, int] = {"01": 1, "09": 1, "21": 1, "22": 2}

_SERVICE_SEP = "/"


def _is_hex(text: str) -> bool:
    return bool(text) and all(c in _HEX_DIGITS for c in text)


def explicit_request(token: str) -> str | None:
    """The request code a ``SERVICE/ID`` token names (``01/0D`` -> ``010D``).

    ``None`` when ``token`` has no ``/``, so it is a bare token the caller resolves
    by its usual rules. Raises :class:`QueryError` when it has one but names an
    unsupported service or an identifier of the wrong width.
    """
    if _SERVICE_SEP not in token:
        return None
    service, _, ident = token.upper().partition(_SERVICE_SEP)
    width = EXPLICIT_SERVICES.get(service)
    if width is None:
        allowed = ", ".join(f"{s}/" for s in EXPLICIT_SERVICES)
        raise QueryError(f"PID {token!r}: service {service!r} is not one of {allowed}")
    if len(ident) != 2 * width or not _is_hex(ident):
        raise QueryError(
            f"PID {token!r}: service {service} takes a {width}-byte identifier "
            f"({2 * width} hex digits), e.g. {service}/{'0D' * width}"
        )
    return service + ident


def inferred_request(token: str) -> str:
    """The request a PID token sends when no definition matches it.

    A ``SERVICE/ID`` token sends exactly what it names. A bare token is either a
    complete read already or an identifier whose service its length implies:

    - two hex digits are a KWP2000 local identifier (``01`` -> ``2101``);
    - four starting with ``21`` are a complete KWP2000 read (``2101``), the
      conventional way to write one;
    - any other four are a service-22 DID (``B001`` -> ``22B001``, ``2201`` ->
      ``222201``, and ``0101`` -> ``220101``, *not* OBD-II mode 01 — write
      ``01/01`` for that);
    - six starting with ``22`` are a complete DID read (``22BC03``).

    Raises :class:`QueryError` for anything else — not hex, an odd number of digits
    (a request is whole bytes, and an ELM327 reads a trailing nibble as a
    response-frame count), or a shape no single read has (``BC0301`` is neither a
    2-byte DID nor a ``22`` request), rather than sending a guess.
    """
    explicit = explicit_request(token)
    if explicit is not None:
        return explicit
    u = token.upper()
    if not _is_hex(u):
        raise QueryError(f"PID {token!r} is not hex")
    if len(u) % 2:
        raise QueryError(
            f"PID {token!r} has an odd number of hex digits (a request is whole bytes)"
        )
    if len(u) == 2:
        return f"21{u}"
    if len(u) == 4:
        return u if u.startswith("21") else f"22{u}"
    if len(u) == 6 and u.startswith("22"):
        return u
    raise QueryError(
        f"PID {token!r} is not defined, and is neither a local identifier (01), a DID "
        "(B001) nor a 2101/22B001 read; write another request as SERVICE/ID (01/0D) or "
        "send it with a raw step (raw 7E4:3E00)"
    )


def _token_names(token: str, pid: str) -> bool:
    """Whether one upper-cased PID ``token`` names the upper-cased ``pid``.

    The one matching rule both the live planner and the capture tools apply. A
    ``SERVICE/ID`` token names only its exact request code; a bare token names a
    PID that starts or ends with it (``22`` names every ``22xxxx`` DID, ``BC03``
    names ``22BC03``), never one it only appears in the middle of.
    """
    explicit = explicit_request(token)
    if explicit is not None:
        return pid == explicit
    return pid.startswith(token) or pid.endswith(token)


def pids_named_by(token: str, keys: Iterable[str]) -> list[str]:
    """The defined PID ``keys`` a PID token picks, in ``keys`` order.

    A ``SERVICE/ID`` token picks only its exact request code. A bare token picks
    a key equal to it if there is one, else every key starting or ending with it
    (``22`` picks every ``22xxxx`` DID, ``BC03`` picks ``22BC03``) — the capture tools'
    rule, so ``VCU:22`` reads what it lists. The exact-key preference is the one
    difference: a token that *is* a defined PID never also drags in another PID it
    happens to end (``0101`` vs ``220101``).
    """
    tok = token.upper()
    upper = [(k, str(k).upper()) for k in keys]
    if explicit_request(tok) is None:
        exact = [k for k, u in upper if u == tok]
        if exact:
            return exact
    return [k for k, u in upper if _token_names(tok, u)]


def looks_like_pid(token: str) -> bool:
    """True if ``token`` looks like a bare PID/DID rather than an ECU name.

    Real ECU names are alphabetic (IGPM, BMS, VCU, …); PIDs/DIDs are hex tokens
    that contain a digit (2101, 22BC07, BC03, C00B, B00E), or a ``SERVICE/ID``
    token (01/0D). A bare hex-with-digit token in the ``ECU`` position is almost
    always a PID accidentally separated from its ECU by a space instead of a
    colon. The single source of truth for the space-vs-colon guard shared by the
    query-step parser and the capture query hint.
    """
    t = token.upper()
    if _SERVICE_SEP in t:
        try:
            return explicit_request(t) is not None
        except QueryError:
            return False
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
        (suffix), without a token matching arbitrarily in the middle. A
        ``SERVICE/ID`` token matches only the request code it names.
        """
        if not self.pids:
            return True
        p = str(pid).upper()
        return any(_token_names(tok, p) for tok in self.pids)

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
        (see :func:`canair.ecus.canonical_ecu_name`).
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
    for pid in pids:
        explicit_request(pid)  # a malformed SERVICE/ID token fails here, not at use
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
