"""Pure text-scanning primitives over captured bytes — a leaf (stdlib only).

Two consumers want the same questions answered about a byte string: ``canair
search`` ("where does this text, VIN or byte pattern occur?") and the ``canair
contribute`` PII pre-flight (:mod:`canlib.pii`, "does this look like a VIN?"). They
share this module so there is exactly one definition of "looks like a VIN" — the
copy that matters is the one gating a public contribution.

Everything works on a **folded** view of the bytes (:func:`ascii_fold`): printable
ASCII is kept and every other byte becomes :data:`PLACEHOLDER`, so an index into the
folded text is an index into the original bytes. That 1:1 mapping is what lets a
text hit be reported as a byte offset.
"""

from __future__ import annotations

import re

Span = tuple[int, int]
"""A half-open ``(start, end)`` range, in characters of a folded text or bytes of data."""

PLACEHOLDER = "\x00"
"""Stands in for every non-printable byte in a folded text."""

_FOLD_TABLE = bytes(b if 0x20 <= b < 0x7F else 0 for b in range(256))

# A VIN is 17 chars, upper alphanumeric excluding I/O/Q (ISO 3779).
VIN_RE = re.compile(r"\b[A-HJ-NPR-Z0-9]{17}\b")
# 4+ identical mask characters in a row — the signature of a redacted value.
_MASK_RUN_RE = re.compile(r"([Xx*#?])\1{3,}")

_TOKEN_CHARS = re.compile(r"[A-Z0-9]")
_TOKEN_PUNCT = re.compile(r"[A-Z0-9 ._/-]")
_MIN_TOKEN_RATIO = 0.6
_MAX_ODD_CHARS = 1


def ascii_fold(data: bytes) -> str:
    """Printable-ASCII view of ``data``, one character per byte.

    Non-printable bytes become :data:`PLACEHOLDER`, so ``folded[i]`` always
    describes ``data[i]`` and a match offset in the text is a byte offset.
    """
    return data.translate(_FOLD_TABLE).decode("ascii")


def printable_runs(data: bytes, min_len: int) -> list[tuple[int, str]]:
    """Maximal runs of at least ``min_len`` printable bytes, as ``(start, text)``."""
    folded = ascii_fold(data)
    pattern = re.compile(rf"[^{PLACEHOLDER}]{{{max(1, min_len)},}}")
    return [(m.start(), m.group()) for m in pattern.finditer(folded)]


def enclosing_run(folded: str, start: int, end: int) -> Span:
    """The maximal printable run of ``folded`` containing ``[start, end)``.

    The span must lie inside one run; a span touching a placeholder is returned
    unchanged, since there is no run to widen to.
    """
    lo, hi = start, end
    while lo > 0 and folded[lo - 1] != PLACEHOLDER:
        lo -= 1
    while hi < len(folded) and folded[hi] != PLACEHOLDER:
        hi += 1
    return lo, hi


def is_plausible_token(text: str) -> bool:
    """Whether a printable run looks like an automotive identifier rather than noise.

    Numeric telemetry lands in printable ASCII constantly (a large share of plausible
    sensor values are 0x20-0x7E), so a bare ``strings(1)`` sweep is dominated by
    coincidence. Real part numbers, software versions and ECU names are mostly
    upper-case alphanumerics: at least 60% of characters in ``[A-Z0-9]`` and at most
    one outside ``[A-Z0-9 ._/-]``. Measured on the bundled Ioniq captures this keeps
    every real string and drops every noise run; without it an inventory is unusable.
    """
    if not text:
        return False
    alnum = len(_TOKEN_CHARS.findall(text))
    if alnum < _MIN_TOKEN_RATIO * len(text):
        return False
    odd = len(text) - len(_TOKEN_PUNCT.findall(text))
    return odd <= _MAX_ODD_CHARS


def vin_spans(text: str) -> list[Span]:
    """Spans of ``text`` that could be a VIN.

    Shape alone is not enough. A VIN's charset is a *superset* of digits, so a
    17-digit ECU serial — the exact format the BSD modules report — matches
    :data:`VIN_RE`. Requiring at least one letter separates the two: every real-world
    WMI carries letters, while an all-numeric 17-char run is a serial.
    """
    return [m.span() for m in VIN_RE.finditer(text) if any(c.isalpha() for c in m.group())]


def has_vin_token(text: str) -> bool:
    """True when ``text`` contains a token that could be a VIN."""
    return bool(vin_spans(text))


def looks_redacted(value: str) -> bool:
    """True when ``value`` carries a run of 4+ identical mask characters.

    An already-scrubbed value (``KMHCXXXXXXXXXXXXX``) is not PII, and re-flagging
    it on every run trains the reviewer to skip the report — the failure mode that
    makes a security check useless. Four identical ``X``/``*``/``#``/``?`` in a row
    does not occur in a real VIN or serial, so this is specific without having to
    know which redaction convention was used.
    """
    return bool(_MASK_RUN_RE.search(value))


def find_literal(text: str, needle: str, *, case_sensitive: bool = False) -> list[Span]:
    """Non-overlapping spans of ``needle`` in ``text`` (case-insensitive by default)."""
    if not needle:
        return []
    hay, pin = (text, needle) if case_sensitive else (text.lower(), needle.lower())
    # ``str.lower`` can change a character's length (``İ``); fall back to a regex
    # then, so a reported span is always an index into the original text.
    if len(hay) != len(text):
        return find_regex(text, re.compile(re.escape(needle), 0 if case_sensitive else re.I))
    spans: list[Span] = []
    i = hay.find(pin)
    while i != -1:
        spans.append((i, i + len(pin)))
        i = hay.find(pin, i + len(pin))
    return spans


def compile_regex(pattern: str, *, case_sensitive: bool = False) -> re.Pattern[str]:
    """Compile a user pattern; raises :class:`re.error` for the caller to report."""
    return re.compile(pattern, 0 if case_sensitive else re.IGNORECASE)


def find_regex(text: str, pattern: re.Pattern[str]) -> list[Span]:
    """Non-empty spans of ``pattern`` in ``text``."""
    return [m.span() for m in pattern.finditer(text) if m.end() > m.start()]


def parse_hex_needle(text: str) -> bytes:
    """Bytes from a hex string, tolerating spaces, ``0x`` prefixes and colons.

    Raises :class:`ValueError` for an empty or non-hex needle.
    """
    cleaned = re.sub(r"0x|[\s:,_-]", "", text, flags=re.I)
    if not cleaned:
        raise ValueError("empty hex pattern")
    if len(cleaned) % 2:
        raise ValueError(f"hex pattern {text!r} has an odd number of digits")
    try:
        return bytes.fromhex(cleaned)
    except ValueError:
        raise ValueError(f"hex pattern {text!r} is not hexadecimal") from None


def find_hex(data: bytes, needle: bytes) -> list[Span]:
    """Non-overlapping byte spans of ``needle`` in ``data``."""
    if not needle:
        return []
    spans: list[Span] = []
    i = data.find(needle)
    while i != -1:
        spans.append((i, i + len(needle)))
        i = data.find(needle, i + len(needle))
    return spans
