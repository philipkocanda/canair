"""Pure text-scanning primitives over captured bytes — a leaf (stdlib only).

Two consumers want the same questions answered about a byte string: ``canair
search`` ("where does this text, VIN or byte pattern occur?") and the ``canair
contribute`` PII pre-flight (:mod:`canlib.pii`, "does this look like a VIN?"). They
share this module so there is exactly one definition of "looks like a VIN" — the
copy that matters is the one gating a public contribution.

Everything works on a **folded** view of the bytes (:func:`ascii_fold`): printable
ASCII is kept and every other byte becomes :data:`PLACEHOLDER`, so an index into the
folded text is an index into the original bytes. That 1:1 mapping is what lets a
text hit be reported as a byte offset. Matchers run on one printable run at a time
(:func:`folded_runs`), so no match can reach across a non-printable byte.
"""

from __future__ import annotations

import re

Span = tuple[int, int]
"""A half-open ``(start, end)`` range, in characters of a folded text or bytes of data."""

PLACEHOLDER = "\x00"
"""Stands in for every non-printable byte in a folded text."""

_FOLD_TABLE = bytes(b if 0x20 <= b < 0x7F else 0 for b in range(256))

# A VIN is 17 chars, upper alphanumeric excluding I/O/Q (ISO 3779). The bounds are
# "not another VIN character" rather than ``\b``: a word boundary misses a VIN glued
# to a lower-case letter or an underscore, and every ``\b``-bounded token is still a
# match (a non-word character is never a VIN character), so this only ever finds more.
_VIN_CHARS = "A-HJ-NPR-Z0-9"
VIN_LEN = 17
VIN_RE = re.compile(rf"(?<![{_VIN_CHARS}])[{_VIN_CHARS}]{{{VIN_LEN}}}(?![{_VIN_CHARS}])")
_VIN_RUN_RE = re.compile(rf"[{_VIN_CHARS}]{{{VIN_LEN},}}")
# Fewer repeated characters than this beside a VIN-length window are ambiguous: one
# extra character could be a trailing pad byte or a leading header byte.
_MIN_PAD = 2
# 4+ identical mask characters in a row — the signature of a redacted value.
_MASK_RUN_RE = re.compile(r"([Xx*#?])\1{3,}")

_TOKEN_CHARS = re.compile(r"[A-Z0-9]")
_TOKEN_PUNCT = re.compile(r"[A-Z0-9 ._/-]")
_MIN_TOKEN_RATIO = 0.6
_MAX_ODD_CHARS = 1


def ascii_fold(data: bytes, start: int = 0) -> str:
    """Printable-ASCII view of ``data``, one character per byte.

    Non-printable bytes become :data:`PLACEHOLDER`, so ``folded[i]`` always
    describes ``data[i]`` and a match offset in the text is a byte offset. The first
    ``start`` bytes are masked too, so a response header (the SID and its identifier
    echo, which is often printable) can never join or start a run.
    """
    folded = data.translate(_FOLD_TABLE).decode("ascii")
    if start > 0:
        head = min(start, len(folded))
        folded = PLACEHOLDER * head + folded[head:]
    return folded


def folded_runs(folded: str, min_len: int = 1) -> list[tuple[int, str]]:
    """Maximal runs of at least ``min_len`` non-placeholder characters of a folded text."""
    pattern = re.compile(rf"[^{PLACEHOLDER}]{{{max(1, min_len)},}}")
    return [(m.start(), m.group()) for m in pattern.finditer(folded)]


def printable_runs(data: bytes, min_len: int, start: int = 0) -> list[tuple[int, str]]:
    """Maximal runs of at least ``min_len`` printable bytes at or after ``start``."""
    return folded_runs(ascii_fold(data, start), min_len)


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


def _has_letter(text: str) -> bool:
    return any(c.isalpha() for c in text)


def vin_spans(text: str) -> list[Span]:
    """Spans of ``text`` that could be a VIN: exactly 17 VIN characters, at least one letter.

    Shape alone is not enough. A VIN's charset is a *superset* of digits, so a
    17-digit ECU serial — the exact format the BSD modules report — matches
    :data:`VIN_RE`. Requiring at least one letter separates the two: every real-world
    WMI carries letters, while an all-numeric 17-char run is a serial.

    A longer run of VIN characters is not a VIN token here: in free text or a hex
    string (``has_vin_token`` also checks a payload's hex) any 18+ character
    alphanumeric run would otherwise qualify. :func:`data_vin_spans` is the variant
    for a printable view of payload bytes, where a VIN's neighbours are arbitrary bytes.
    """
    return [m.span() for m in VIN_RE.finditer(text) if _has_letter(m.group())]


def data_vin_spans(text: str) -> list[Span]:
    """VIN spans in a printable view of payload *bytes* — :func:`vin_spans` and more.

    In a payload the bytes beside a VIN are whatever the ECU put there, and some are
    VIN characters: a ``0x55`` (``U``) fill byte, or a printable identifier echo. So
    every run of 17 or more VIN characters that carries a letter is reported:

    * exactly 17 — the VIN itself, as :func:`vin_spans` finds it;
    * longer, where the excess is a repeat of one character (at least two of it) at
      one end — the 17-character window beside that padding (trailing padding wins
      when both ends look like it, since fill bytes usually follow the data);
    * any other longer run — the whole run, since no window is more likely than
      another (one extra character could equally be a pad byte or a header byte).

    Every :func:`vin_spans` hit is therefore also a hit here, so the PII gate that
    uses this for payloads can only catch more. Measured on the bundled corpus there
    is no payload run longer than 17 VIN characters, so the wider net costs nothing
    there.
    """
    spans: list[Span] = []
    for m in _VIN_RUN_RE.finditer(text):
        run, base = m.group(), m.start()
        if not _has_letter(run):
            continue
        lo, hi = _vin_window(run)
        if _has_letter(run[lo:hi]):
            spans.append((base + lo, base + hi))
        else:
            spans.append((base, base + len(run)))
    return spans


def _vin_window(run: str) -> Span:
    """The VIN-length window of a VIN-character ``run``, trimming one-character padding."""
    excess = len(run) - VIN_LEN
    if excess == 0:
        return 0, VIN_LEN
    if excess >= _MIN_PAD:
        if len(set(run[VIN_LEN:])) == 1:
            return 0, VIN_LEN
        if len(set(run[:excess])) == 1:
            return excess, len(run)
    return 0, len(run)


def has_vin_token(text: str) -> bool:
    """True when ``text`` contains a token that could be a VIN."""
    return bool(vin_spans(text))


def has_data_vin(text: str) -> bool:
    """True when a printable view of payload bytes holds something VIN-shaped."""
    return bool(data_vin_spans(text))


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


_HEX_SEPARATORS = re.compile(r"[\s:,_-]+")
_HEX_PREFIX = re.compile(r"0x", re.IGNORECASE)
_HEX_DIGITS = re.compile(r"[0-9a-fA-F]+")


def parse_hex_needle(text: str) -> bytes:
    """Bytes from a hex string, tolerating spaces, colons, commas, ``_``/``-`` and ``0x``.

    The separators split the pattern into tokens (``0x`` starts a new one even with no
    space before it, as in ``0xDE0xAD``), and every token must be a whole number of
    bytes. A one-digit token is refused rather than zero-padded: ``'1 2 3 4'`` could
    mean ``01020304`` or ``1234``, and a byte search that silently picked one would
    report the wrong locations with full confidence.

    Raises :class:`ValueError` for an empty, non-hex or odd-digit pattern.
    """
    tokens = [
        piece
        for chunk in _HEX_SEPARATORS.split(text.strip())
        for piece in _HEX_PREFIX.split(chunk)
        if piece
    ]
    if not tokens:
        raise ValueError("empty hex pattern")
    for token in tokens:
        if not _HEX_DIGITS.fullmatch(token):
            raise ValueError(f"hex pattern {text!r} is not hexadecimal")
        if len(token) % 2:
            raise ValueError(
                f"hex pattern {text!r}: {token!r} has an odd number of digits — "
                f"write every byte as two (e.g. '0{token}')"
            )
    return bytes.fromhex("".join(tokens))


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
