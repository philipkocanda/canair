"""The parsed-response shape every transport returns, and its outcome taxonomy.

:class:`UdsResponse` is what :func:`canair.uds.parse.parse_uds_response` builds and
every ``Terminal.send_uds`` returns, so downstream code sees one shape whichever
transport produced the bytes. Each response falls into exactly one
:data:`RESPONSE_CATEGORIES` value; :func:`classify_response` is the single
classifier behind the diagnostics recorder (:class:`canair.transport.stats.TransportStats`),
the monitor's live ``drops`` indicator and a recorded session's ``quality`` counts.
"""

from __future__ import annotations

from typing import Final, Literal, NotRequired, TypedDict

ResponseCategory = Literal["ok", "nrc", "no_data", "drop", "stale", "decode", "bus", "other"]

# On a failure the category is also stamped onto the response as ``error_kind`` at
# the error site, so classification never has to re-parse an error *string*.
#
# - ok: a positive, echo-matching response
# - nrc: a legitimate negative response (7F …) — an answer, not a fault
# - no_data: NO DATA / empty — the ECU said nothing
# - drop: an ISO-TP consecutive frame dropped (truncated reassembly)
# - stale: a stale/leaked frame — non-contiguous counters, SID/echo mismatch
# - decode: a non-hex / too-short / undecodable payload
# - bus: CAN ERROR / unable to connect / buffer full / adapter refusal (`?`)
# - other: the unclassified fallback
CAT_OK: Final[ResponseCategory] = "ok"
CAT_NRC: Final[ResponseCategory] = "nrc"
CAT_NO_DATA: Final[ResponseCategory] = "no_data"
CAT_DROP: Final[ResponseCategory] = "drop"
CAT_STALE: Final[ResponseCategory] = "stale"
CAT_DECODE: Final[ResponseCategory] = "decode"
CAT_BUS: Final[ResponseCategory] = "bus"
CAT_OTHER: Final[ResponseCategory] = "other"

# All categories a recorded exchange can fall into (drives TransportStats init).
RESPONSE_CATEGORIES: tuple[ResponseCategory, ...] = (
    CAT_OK,
    CAT_NRC,
    CAT_NO_DATA,
    CAT_DROP,
    CAT_STALE,
    CAT_DECODE,
    CAT_BUS,
    CAT_OTHER,
)

# The subset that means something went wrong (not a clean positive / valid NRC).
ERROR_CATEGORIES: tuple[ResponseCategory, ...] = (
    CAT_NO_DATA,
    CAT_DROP,
    CAT_STALE,
    CAT_DECODE,
    CAT_BUS,
    CAT_OTHER,
)


class UdsResponse(TypedDict):
    """Structured result of :func:`~canair.uds.parse.parse_uds_response`.

    ``raw``/``ok`` are always present; the rest depend on the outcome:

    - a parseable positive carries ``hex``/``bytes`` (and ``isotp_declared_len``
      on a multi-frame read that reported an ISO-TP First Frame length);
    - a negative (``7F``) carries ``nrc``/``nrc_service``/``nrc_desc`` — unless
      the NRC echoes a *different* service, in which case those are dropped and
      ``error`` is set instead;
    - a parse failure / ``NO DATA`` / echo mismatch carries ``error`` and ``error_kind``;
    - a request with the suppress-positive-response bit that drew no NRC is ``ok`` with
      ``suppressed`` and an empty ``hex``/``bytes``.
    """

    raw: str  # always: original response text
    ok: bool  # always: positive + echo-matching?
    hex: NotRequired[str]  # parseable positive: uppercased, space-stripped
    bytes: NotRequired[bytes]  # parseable positive
    isotp_declared_len: NotRequired[int]  # multi-frame: ISO-TP First Frame length
    # CAN frames the reply occupied: the frame lines an ELM327 printed, or (raw ISO-TP)
    # derived from the payload length by the terminal
    isotp_frame_count: NotRequired[int]
    nrc: NotRequired[int]  # negative (7F): the NRC byte
    nrc_service: NotRequired[int]  # negative: echoed service byte
    nrc_desc: NotRequired[str]  # negative: human description
    error: NotRequired[str]  # parse failure / NO DATA / echo mismatch
    error_kind: NotRequired[ResponseCategory]  # failure category, set with `error`
    # The request set the suppress-positive-response bit and no NRC came: ``ok`` with an
    # empty payload, since by the standard no reply is the positive answer.
    suppressed: NotRequired[bool]


def classify_response(resp: UdsResponse) -> ResponseCategory:
    """Classify a parsed response into one :data:`RESPONSE_CATEGORIES` value.

    A clean positive is ``ok`` and a legitimate negative (``7F …``) is ``nrc``
    (an answer, not a fault). Every other outcome reports the ``error_kind``
    stamped at the point of failure; for a response carrying only an ``error``
    string (built outside the parser) it falls back to :func:`classify_error`
    on that text, and finally to ``other`` so a total never silently disappears.
    """
    if resp.get("ok"):
        return CAT_OK
    if resp.get("nrc") is not None:
        return CAT_NRC
    return resp.get("error_kind") or classify_error(resp.get("error"))


# Ordered (substring → category) rules matched case-insensitively against an
# error string. First match wins, so order matters (drops before the generic
# reassembly wording, mismatch before the rest). They must agree with the
# ``error_kind`` the parser stamps for the same message, which
# ``tests/uds/test_uds_parse_adapter_text.py`` checks for every message it can produce.
_ERROR_RULES: tuple[tuple[str, ResponseCategory], ...] = (
    ("non-contiguous iso-tp", CAT_DROP),
    ("truncated iso-tp", CAT_DROP),
    ("mismatch", CAT_STALE),
    # Several single-frame replies the parser could not pick one answer from.
    ("separate replies", CAT_STALE),
    ("different replies answer", CAT_STALE),
    ("another request's echo", CAT_STALE),
    ("no data", CAT_NO_DATA),
    ("no response", CAT_NO_DATA),
    ("empty response", CAT_NO_DATA),
    ("timeout", CAT_NO_DATA),
    ("can bus error", CAT_BUS),
    ("bus busy", CAT_BUS),
    ("unable to connect", CAT_BUS),
    ("bus initialization", CAT_BUS),
    ("connection closed", CAT_BUS),
    ("request stopped", CAT_BUS),
    ("buffer full", CAT_BUS),
    ("unknown command", CAT_BUS),
    ("non-hex", CAT_DECODE),
    ("hex decode", CAT_DECODE),
    ("too short", CAT_DECODE),
    ("odd hex", CAT_DECODE),
)


def classify_error(error: str | None) -> ResponseCategory:
    """Classify an error string into an :data:`ERROR_CATEGORIES` bucket.

    Matches known substrings emitted by the parser (and the raw clients); anything
    unrecognised falls back to ``other`` so a total never silently disappears.
    """
    if not error:
        return CAT_OTHER
    low = error.lower()
    for needle, category in _ERROR_RULES:
        if needle in low:
            return category
    return CAT_OTHER
