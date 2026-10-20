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
    find_hex,
    find_literal,
    find_regex,
    parse_hex_needle,
    vin_spans,
)

from .findings import ByteSpans, TextSpans

IN_PAYLOAD = "payload"
IN_TEXT = "text"
IN_ALL = "all"
IN_CHOICES = (IN_PAYLOAD, IN_TEXT, IN_ALL)


class UsageError(ValueError):
    """A flag combination that cannot mean anything; reported as exit code 2."""


@dataclass(frozen=True)
class Needle:
    """What to look for: a text matcher, a byte matcher, or (for ``--hex``) only bytes."""

    label: str
    text: TextSpans | None = None
    data: ByteSpans | None = None


def build_needle(args: argparse.Namespace) -> Needle | None:
    """The needle for ``args``, or ``None`` for inventory mode (no pattern at all).

    Raises :class:`UsageError` for contradictory or unusable flags.
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

    if hex_arg is not None:
        if args.regex or args.case_sensitive:
            raise UsageError("--hex matches bytes; --regex and --case-sensitive do not apply")
        if args.in_ == IN_TEXT:
            raise UsageError("--hex matches payload bytes; it cannot search --in text")
        try:
            needle = parse_hex_needle(hex_arg)
        except ValueError as e:
            raise UsageError(str(e)) from None
        return Needle(label=f"hex {needle.hex().upper()}", data=lambda b: find_hex(b, needle))

    if args.vin:
        return Needle(label="VIN", text=vin_spans)

    if pattern is None:
        if args.defs:
            raise UsageError(
                "--defs needs a PATTERN (it searches definitions, it does not list them)"
            )
        if args.in_ == IN_TEXT:
            raise UsageError("--in text needs a PATTERN — free text has nothing to inventory")
        return None

    if args.regex:
        try:
            compiled = compile_regex(pattern, case_sensitive=args.case_sensitive)
        except re.error as e:
            raise UsageError(f"invalid regular expression {pattern!r}: {e}") from None
        return Needle(label=f"/{pattern}/", text=lambda t: find_regex(t, compiled))

    return Needle(
        label=repr(pattern),
        text=lambda t: find_literal(t, pattern, case_sensitive=args.case_sensitive),
    )
