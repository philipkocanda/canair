"""The shared ``--notation`` flag and the effective notation for a command.

The notations themselves (:class:`~canair.expression.notation.ByteNotation`) are protocol code; reading
the ``display.byte_notation`` config key and registering the flag are the CLI's part.
"""

from __future__ import annotations

from canair.config.file import get_config_key
from canair.expression.notation import ByteNotation


def add_notation_arg(parser) -> None:
    """Register the shared ``--notation`` flag on an analysis command's parser.

    Default is ``None`` so :func:`resolve_notation` can fall back to the
    ``display.byte_notation`` config key (then ``wican``).
    """
    parser.add_argument(
        "--notation",
        choices=[n.value for n in ByteNotation],
        default=None,
        metavar="NAME",
        help="byte-index notation for output labels: wican (default), isotp, "
        "torque, bix. Overrides the display.byte_notation config key.",
    )


def resolve_notation(value: str | None) -> ByteNotation:
    """Effective notation: explicit ``--notation`` > ``display.byte_notation`` > wican."""
    if value:
        return ByteNotation(value)

    configured = get_config_key("display.byte_notation")
    if configured:
        try:
            return ByteNotation(str(configured))
        except ValueError:
            pass
    return ByteNotation.WICAN
