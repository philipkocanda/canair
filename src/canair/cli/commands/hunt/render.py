"""How a hunt hit is labelled."""

from __future__ import annotations

from canair.expression.notation import ByteNotation, ByteRef


def hit_label(h, notation: ByteNotation, sub_bytes: int, payload_len: int | None = None) -> str:
    """Render a hunt hit's byte in ``notation``.

    WiCAN shows the promotable expression as-is (``h.expr``); other notations
    render the byte position (the expression stays available via --json/--promote,
    which always emit the canonical WiCAN form). ``payload_len`` selects the frame
    layout so a single-frame response is indexed correctly.
    """
    if notation is ByteNotation.WICAN:
        return h.expr
    return ByteRef.from_wican(h.offset, width=h.width, payload_len=payload_len).render(
        notation, sub_bytes=sub_bytes
    )
