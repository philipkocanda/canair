"""Turn ``canair search``'s flags into the matchers the scan runs.

Kept apart from the command so the flag rules (which combinations are meaningless)
are one small, testable unit with no I/O.
"""

from __future__ import annotations

import argparse
import re
from dataclasses import dataclass

from canlib.textscan import (
    compile_regex,
    data_vin_spans,
    find_hex,
    find_literal,
    find_regex,
    parse_hex_needle,
    vin_spans,
)

from .findings import ByteSpans, MatchKey, TextSpans

IN_PAYLOAD = "payload"
IN_TEXT = "text"
IN_ALL = "all"
IN_CHOICES = (IN_PAYLOAD, IN_TEXT, IN_ALL)

DEFAULT_MIN_LEN = 6


class UsageError(ValueError):
    """A flag combination that cannot mean anything; reported as exit code 2."""


@dataclass(frozen=True)
class Needle:
    """What to look for: a text matcher, a byte matcher, or (for ``--hex``) only bytes.

    ``payload`` overrides ``text`` for the printable runs of payload bytes, where a
    match's neighbours are arbitrary bytes rather than prose (the VIN matcher differs
    there). ``match_key`` says which matched readings count as the same hit.
    """

    label: str
    text: TextSpans | None = None
    data: ByteSpans | None = None
    payload: TextSpans | None = None
    match_key: MatchKey | None = None

    @property
    def payload_text(self) -> TextSpans | None:
        return self.payload or self.text


def build_needle(args: argparse.Namespace) -> Needle | None:
    """The needle for ``args``, or ``None`` for inventory mode (no pattern at all).

    Raises :class:`UsageError` for contradictory or unusable flags — including a flag
    the chosen mode would silently ignore, since a quietly dropped option reads as
    "applied, found nothing".
    """
    pattern: str | None = args.pattern
    hex_arg: str | None = args.hex
    given = [
        name for name, on in (("PATTERN", pattern), ("--hex", hex_arg), ("--vin", args.vin)) if on
    ]
    if len(given) > 1:
        raise UsageError(f"{' and '.join(given)} are mutually exclusive — pick one needle")
    if args.regex and not pattern:
        raise UsageError("--regex needs a PATTERN")
    if given:
        inventory_only = [
            flag
            for flag, on in (("--min-len", args.min_len is not None), ("--all-runs", args.all_runs))
            if on
        ]
        if inventory_only:
            raise UsageError(
                f"{' and '.join(inventory_only)} only apply to the inventory (no PATTERN, "
                f"--hex or --vin); drop {'them' if len(inventory_only) > 1 else 'it'} or "
                f"the needle"
            )

    if hex_arg is not None:
        if args.regex or args.case_sensitive:
            raise UsageError("--hex matches bytes; --regex and --case-sensitive do not apply")
        if args.in_ == IN_TEXT:
            raise UsageError("--hex matches payload bytes; it cannot search --in text")
        if args.defs:
            raise UsageError("--hex matches payload bytes; --defs searches identity text")
        try:
            needle = parse_hex_needle(hex_arg)
        except ValueError as e:
            raise UsageError(str(e)) from None
        return Needle(label=f"hex {needle.hex().upper()}", data=lambda b: find_hex(b, needle))

    if args.vin:
        if args.case_sensitive:
            raise UsageError("--vin is upper-case by definition; --case-sensitive does not apply")
        return Needle(label="VIN", text=vin_spans, payload=data_vin_spans)

    if pattern is None:
        if args.defs:
            raise UsageError(
                "--defs needs a PATTERN (it searches definitions, it does not list them)"
            )
        if args.in_ == IN_TEXT:
            raise UsageError("--in text needs a PATTERN — free text has nothing to inventory")
        if args.case_sensitive:
            raise UsageError("--case-sensitive needs a PATTERN — the inventory matches nothing")
        return None

    fold = None if args.case_sensitive else str.lower
    if args.regex:
        try:
            compiled = compile_regex(pattern, case_sensitive=args.case_sensitive)
        except re.error as e:
            raise UsageError(f"invalid regular expression {pattern!r}: {e}") from None
        return Needle(label=f"/{pattern}/", text=lambda t: find_regex(t, compiled), match_key=fold)

    return Needle(
        label=repr(pattern),
        text=lambda t: find_literal(t, pattern, case_sensitive=args.case_sensitive),
        match_key=fold,
    )
