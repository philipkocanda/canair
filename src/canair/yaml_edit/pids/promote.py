"""Promoting an ``iocontrol_discoveries:`` entry into a curated ``iocontrol:`` entry.

Split from :mod:`canair.yaml_edit.pids.hits` (the scanner-section appends and field edits);
builds on the same primitives in :mod:`canair.yaml_edit.text`.
"""

from __future__ import annotations

import re
from pathlib import Path

from canair.profile import Profile
from canair.yaml_edit.text import (
    PidsEditError,
    _cut_block,
    _find_ecu_block,
    _find_section_entry,
    _format_label,
    _insert_lines,
    _key_token,
    _keyed_block,
    find_ecu_file,
    write_ecu_text,
)

# ── Discovery promotion ──────────────────────────────────────────────────────


def _find_discovery_block(text: str, did: str) -> tuple[int, int]:
    """Find the character range of a DID entry inside ``iocontrol_discoveries:``.

    Returns (start, end) spanning from the DID key line (bare or quoted) up to
    (but not including) the next sibling / dedent, or the comments heading it.
    Raises ``PidsEditError`` if the DID isn't found under ``iocontrol_discoveries:``.
    """
    return _find_section_entry(text, "iocontrol_discoveries", did, noun="DID")


def _count_discovery_entries(text: str) -> int:
    """Count DID entries remaining in the iocontrol_discoveries section."""
    sec = _keyed_block(text, "iocontrol_discoveries", 2, 0, len(text))
    if not sec:
        return 0
    return len(re.findall(r"^ {4}[^\s#]", text[sec[2] : sec[3]], re.MULTILINE))


def _remove_discovery_section(text: str) -> str:
    """Drop the entire ``iocontrol_discoveries:`` block (header + body)."""
    sec = _keyed_block(text, "iocontrol_discoveries", 2, 0, len(text))
    return _cut_block(text, sec[0], sec[3]) if sec else text


def _infer_on_payload(discovery_block: str) -> str:
    """Infer the ``on`` payload hex from a discovery's captured response.

    The 0x2F positive response is ``6F {DID_hi} {DID_lo} {controlStateRecord...}``
    so the trailing bytes after the 3-byte echo *are* the controlStateRecord
    the ECU reported as current state. Replaying that record back as a
    shortTermAdjustment payload (``2F{DID}03{tail}``) is a guaranteed
    in-range, safe probe — it asserts "make state == current state" — and
    avoids NRC 0x31 requestOutOfRange rejections that the previous
    ``FF x nbytes`` default triggered.

    The user then refines the payload via the ``e`` edit flow once the real
    controlState semantics are understood. Falls back to a single ``00`` byte
    if the response is missing, too short, or malformed — never FF.
    """
    m = re.search(r'^ {6}response:\s*"([0-9A-Fa-f]*)"\s*$', discovery_block, re.MULTILINE)
    if not m:
        return "00"
    hex_str = m.group(1).upper()
    if len(hex_str) % 2 != 0 or len(hex_str) < 8:
        # Need at least the 3-byte echo (6 hex chars) + 1 state byte (2 hex).
        return "00"
    return hex_str[6:]


def promote_discovery(
    ecu_name: str, did: str, label: str, pids_dir: Path | None = None, *, profile: Profile
) -> Path:
    """Promote a discovery DID to a curated ``iocontrol:`` entry.

    Removes the DID entry from ``iocontrol_discoveries:`` and appends a new
    curated entry to ``iocontrol:`` with inferred sub-functions:

    - ``on``:  ``2F{DID}03{tail}``   (shortTermAdjustment replaying the
      captured controlStateRecord — safe, in-range, never NRC 31)
    - ``off``: ``2F{DID}00``          (returnControlToECU)
    - ``verified: false``

    The ``tail`` is the bytes after the ``6F{DID}`` echo of the captured
    response. Replaying current state is harmless; the user refines the
    payload via the ``e`` edit flow once effects are observed. Falls back
    to ``00`` if the response is missing/malformed.

    Raises ``PidsEditError`` if the ECU or DID isn't found, or if the DID
    already exists under curated ``iocontrol:``.
    """
    did_u = did.strip().upper()
    if re.fullmatch(r"[0-9A-F]{2}", did_u):
        raise PidsEditError(
            f"{did_u} is a KWP2000 local identifier; promotion only infers UDS 0x2F "
            "commands, and KWP2000 actuator layouts are make-specific"
        )
    if not re.fullmatch(r"[0-9A-F]{4}", did_u):
        raise PidsEditError(f"DID must be 4 hex digits, got {did!r}")
    label = label.strip()
    if not label:
        raise PidsEditError("label must not be empty")

    fpath = find_ecu_file(ecu_name, pids_dir=pids_dir, profile=profile)
    text = fpath.read_text()
    ecu_start, ecu_end = _find_ecu_block(text, ecu_name)
    ecu_block = text[ecu_start:ecu_end]

    # Guard against clobbering an existing curated entry (bare or quoted key).
    curated = _keyed_block(ecu_block, "iocontrol", 2, 0, len(ecu_block))
    if curated and _keyed_block(ecu_block, did_u, 4, curated[2], curated[3]):
        raise PidsEditError(f"DID {did_u} already exists under curated iocontrol: — cannot promote")

    # Find the discovery block and infer state-byte length from its response.
    d_start, d_end = _find_discovery_block(ecu_block, did_u)
    on_payload = _infer_on_payload(ecu_block[d_start:d_end])
    without_disc = _cut_block(ecu_block, d_start, d_end)

    # If the discoveries section is now empty, remove it entirely.
    if _count_discovery_entries(without_disc) == 0:
        without_disc = _remove_discovery_section(without_disc)

    # Build the new curated entry (4-space indent inside "  iocontrol:").
    new_entry = [
        f"    {_key_token(did_u)}:",
        f"      label: {_format_label(label)}",
        "      verified: false",
        f'      on: "2F{did_u}03{on_payload}"',
        f'      off: "2F{did_u}00"',
    ]

    # Append the entry to the iocontrol: section — located afresh, since removing
    # the discovery shifted every offset after it — or create the section.
    curated = _keyed_block(without_disc, "iocontrol", 2, 0, len(without_disc))
    if curated:
        new_block = _insert_lines(without_disc, curated[2], curated[3], new_entry)
    else:
        body = without_disc.rstrip("\n")
        new_block = body + "\n\n  iocontrol:\n" + "".join(ln + "\n" for ln in new_entry)

    new_text = text[:ecu_start] + new_block + text[ecu_end:]
    write_ecu_text(fpath, text, new_text, ecu_name)
    return fpath
