"""The shape of a UDS service-22 multi-DID reply, independent of who asked for it.

A ``22 D1 D2 …`` request is answered ``62 D1 <data1> D2 <data2> …``: one SID, then each DID
followed by its data, with nothing marking where one DID's data ends. Splitting therefore needs
each DID's data length, and :func:`split_chain` walks the reply with whatever lengths the caller
knows. Two kinds of caller need that walk:

- the live batched read path, to tell a leaked batch reply from the single-DID reply it is
  pretending to be (both start ``62 D1``, so the echo check passes it); and
- ``validate captures``, to find such a reply already stored under its first DID.

Pure and device-free. :func:`canlib.modes.multi_batch.split_multi_did` is the stricter sibling
for a reply to a known request, where the DIDs and their order are given rather than discovered.
"""

from __future__ import annotations

from collections.abc import Callable

from canlib.frame_counts import frames_for_payload

POSITIVE_READ_SID = 0x62

# Bytes an ISO-TP sender pads a final frame with. Captures before 2026-07-28 kept that padding,
# so a stored batch reply may trail one of these after its last DID.
FILL_BYTES = frozenset({0x00, 0x55, 0xAA, 0xCC, 0xFF})


def is_fill(tail: bytes) -> bool:
    """Is ``tail`` empty, or one repeated ISO-TP fill byte?"""
    return not tail or (len(set(tail)) == 1 and tail[0] in FILL_BYTES)


def is_padding(payload: bytes, length: int) -> bool:
    """Could ``payload`` be a ``length``-byte reply stored with its ISO-TP padding?

    Padding only ever fills out a reply's final classic-CAN frame, so beyond being fill the
    tail must end exactly at a frame boundary and must not reach back into an earlier frame.
    That is what tells a padded reply from a longer one whose data merely ends in ``00``s.
    """
    n = len(payload)
    if n == length:
        return True
    return (
        n > length
        and is_fill(payload[length:])
        and frames_for_payload(n) == frames_for_payload(length)
        and frames_for_payload(n + 1) > frames_for_payload(n)
    )


def split_chain(
    payload: bytes, data_len: Callable[[str], int | None]
) -> list[tuple[str, bytes]] | None:
    """Split ``payload`` into its DIDs if it is a reply to two or more of them.

    ``data_len`` maps a DID (4 upper-case hex chars) to its known data length, or ``None`` when
    the length is unknown. A length below one byte counts as unknown: a DID's data record is
    never empty, and a zero-length DID would let any two bytes that happen to equal its ID
    chain. The walk starts at the first DID and follows each data block to the next DID; it
    succeeds only when at least two DIDs chain and the whole payload is consumed, bar trailing
    ISO-TP fill. Returns ``[(DID4, single-DID reply bytes), …]``, each reply shaped
    ``62 DID <data>`` so ordinary decoders read it unchanged, or ``None``.

    Requiring the exact consumption is what makes it unlikely that a single-DID reply whose data
    happens to contain a known DID's two bytes is mistaken for a batch: the bytes after them
    must also land exactly on the payload's end (or on another known DID). It is a heuristic
    over the lengths it is given, so it is only as sound as those lengths.
    """
    if len(payload) < 3 or payload[0] != POSITIVE_READ_SID:
        return None
    out: list[tuple[str, bytes]] = []
    pos = 1
    while pos + 2 <= len(payload):
        did = payload[pos : pos + 2].hex().upper()
        dlen = data_len(did)
        if dlen is None or dlen < 1 or pos + 2 + dlen > len(payload):
            break
        out.append((did, bytes([POSITIVE_READ_SID]) + payload[pos : pos + 2 + dlen]))
        pos += 2 + dlen
    if len(out) < 2 or not is_fill(payload[pos:]):
        return None
    return out
