"""Scanner-section editors: routines / iocontrol_discoveries / sessions.

Appends and field-edits for the machine-discovered sections written by
``canair scan``/``discover`` (routines, iocontrol_discoveries, sessions), plus
promotion of a discovery into a real iocontrol entry. Builds on the shared
primitives in :mod:`canlib.pids_edit._text`.
"""

from __future__ import annotations

import re
from pathlib import Path

from ._text import (
    PidsEditError,
    _cut_block,
    _find_did_block,
    _find_ecu_block,
    _find_section_entry,
    _format_block_scalar,
    _format_label,
    _format_verified,
    _insert_lines,
    _invalidate,
    _key_token,
    _keyed_block,
    _replace_field_in_block,
    _write_text,
    find_ecu_file,
)

# IOControl fields editable from the TUI.
EDITABLE_FIELDS = ("label", "verified", "notes")

# Every curated iocontrol: field `canair pids set-iocontrol` can write.
IOCONTROL_FIELDS = (
    *EDITABLE_FIELDS,
    "on",
    "off",
    "release",
    "session",
    "session_mode",
    "hold",
    "status_param",
)
_IOCONTROL_BOOL_FIELDS = frozenset({"verified", "session", "hold"})
# Hex strings stay quoted: a bare `03` or `30070001` would re-parse as an int.
_IOCONTROL_QUOTED_FIELDS = frozenset({"on", "off", "release", "session_mode"})


# ── Routines section appender ────────────────────────────────────────────────


def _format_routines_block(hits) -> list[str]:
    """Deprecated: use ``_format_hit_block(hits, 'routines', 'rid')`` instead.

    Kept as a thin shim for backward compatibility with any external callers.
    """
    return _format_hit_block(hits, "routines", "rid")


def append_routines_block(
    ecu_name: str, hits, pids_dir: Path | None = None, key_width: int = 4
) -> Path:
    """Write/overwrite a ``routines:`` section at the end of the ECU block.

    If a ``routines:`` section already exists for this ECU, it is replaced
    wholesale. Preserves surrounding YAML (pids/iocontrol/research blocks).

    ``key_width`` is the number of hex digits for the entry key: 4 for UDS
    16-bit Routine Identifiers (``0x31``), 2 for KWP2000 8-bit routine local
    identifiers (``0x33``).

    Returns the file path edited. No-op if ``hits`` is empty.
    """
    return _append_hit_block(
        ecu_name=ecu_name,
        hits=hits,
        section_name="routines",
        key_attr="rid",
        pids_dir=pids_dir,
        key_width=key_width,
    )


def append_iocontrol_discoveries_block(
    ecu_name: str, hits, pids_dir: Path | None = None, key_width: int = 4
) -> Path:
    """Write/overwrite an ``iocontrol_discoveries:`` section at the end of
    the ECU block.

    Kept distinct from the curated ``iocontrol:`` block so the IOControl DID/LID
    scanner can rerun without clobbering human-authored on/off/notes entries.
    Promotion from a discovery to a fully-fledged iocontrol entry is a
    manual, per-DID step.

    ``key_width`` is the number of hex digits used for the entry key: 4 for
    UDS 16-bit DIDs (``0x2F``), 2 for KWP2000 8-bit local identifiers (``0x30``).

    Returns the file path edited. No-op if ``hits`` is empty.
    """
    return _append_hit_block(
        ecu_name=ecu_name,
        hits=hits,
        section_name="iocontrol_discoveries",
        key_attr="did",
        pids_dir=pids_dir,
        key_width=key_width,
    )


def _format_session_entry(hit) -> list[str]:
    """Render one ``sessions:`` entry (2-space key indent, 4-space fields).

    ``hit`` exposes ``.mode`` (int 0x10 sub-function), ``.name`` (str|None),
    ``.supported`` (bool), ``.nrc`` (int|None) and ``.nrc_desc`` (str|None).
    Session-mode keys are always quoted so all-digit values like ``03``/``81``
    keep their intended hex string form rather than being read as YAML ints.
    """
    mode_hex = f"{hit.mode:02X}"
    lines = [f'    "{mode_hex}":']
    if getattr(hit, "name", None):
        name = str(hit.name).replace('"', '\\"')
        lines.append(f'      name: "{name}"')
    lines.append(f"      supported: {'true' if hit.supported else 'false'}")
    if not hit.supported and hit.nrc is not None:
        lines.append(f"      nrc: 0x{hit.nrc:02X}")
        desc = (hit.nrc_desc or "").replace('"', '\\"')
        lines.append(f'      nrc_desc: "{desc}"')
    return lines


def append_sessions_block(ecu_name: str, hits, pids_dir: Path | None = None) -> Path:
    """Write/merge a ``sessions:`` section for one ECU (comment-preserving).

    Records which DiagnosticSessionControl (service 0x10) sub-functions the ECU
    supports, as probed by ``canair scan sessions``. MERGE semantics: existing
    entries are preserved, entries in ``hits`` upsert by session mode, output is
    sorted by mode ascending. No-op if ``hits`` is empty.

    Each hit must expose ``.mode`` (int), ``.name`` (str|None), ``.supported``
    (bool), ``.nrc`` (int|None) and ``.nrc_desc`` (str|None).
    """
    if not hits:
        return find_ecu_file(ecu_name, pids_dir=pids_dir)

    fpath = find_ecu_file(ecu_name, pids_dir=pids_dir)
    text = fpath.read_text()
    ecu_start, ecu_end = _find_ecu_block(text, ecu_name)
    ecu_block = text[ecu_start:ecu_end]

    # Parse any pre-existing ``  sessions:`` section within the ECU block, keeping
    # each entry's raw lines so hand-edited notes/name/state survive a re-scan.
    existing_entries: dict[str, list[str]] = {}
    sec = _keyed_block(ecu_block, "sessions", 2, 0, len(ecu_block))
    if sec:
        section_body = ecu_block[sec[2] : sec[3]]
        entry_re = re.compile(r'^ {4}(["\']?)([0-9A-Fa-f]{1,2})\1:\s*$', re.MULTILINE)
        matches = list(entry_re.finditer(section_body))
        for i, em in enumerate(matches):
            key = em.group(2).upper().zfill(2)
            start = em.start()
            end = matches[i + 1].start() if i + 1 < len(matches) else len(section_body)
            block = section_body[start:end].rstrip("\n")
            existing_entries[key] = block.split("\n")
        # Strip the old section; a merged one is reappended below.
        ecu_block = _cut_block(ecu_block, sec[0], sec[3])

    merged: dict[str, list[str]] = dict(existing_entries)
    for hit in hits:
        merged[f"{hit.mode:02X}"] = _format_session_entry(hit)

    out_lines: list[str] = ["  sessions:"]
    for key in sorted(merged.keys()):
        out_lines.extend(merged[key])
    new_section = "\n".join(out_lines) + "\n"

    body = ecu_block.rstrip("\n")
    new_ecu_block = body + "\n\n" + new_section

    new_text = text[:ecu_start] + new_ecu_block + text[ecu_end:]
    _write_text(fpath, new_text)
    _invalidate()
    return fpath


def _format_hit_entry(hit, key_attr: str, key_width: int = 4) -> list[str]:
    """Render a single hit entry (4-space indent for key, 6 for fields).

    Narrow (KWP 2-digit) keys are always quoted so all-digit local identifiers
    like ``30``/``80`` read as the hex strings they are. A 4-digit key is quoted
    when a bare one wouldn't read back as itself (:func:`_key_token`): ``0203``
    is the octal int 131, ``0700`` int 448, ``1E00`` the float 1.0.
    """
    key_val = getattr(hit, key_attr)
    key_hex = f"{key_val:0{key_width}X}"
    key_token = f'"{key_hex}"' if key_width < 4 else _key_token(key_hex)
    lines = [f"    {key_token}:"]
    if hit.nrc is None:
        resp = hit.response_hex or ""
        lines.append(f'      response: "{resp}"')
    else:
        lines.append(f"      nrc: 0x{hit.nrc:02X}")
        desc = (hit.nrc_desc or "").replace('"', '\\"')
        lines.append(f'      nrc_desc: "{desc}"')
    lines.append('      notes: ""')
    return lines


def _format_hit_block(hits, section_name: str, key_attr: str) -> list[str]:
    """Render a hit-block (routines or iocontrol_discoveries).

    Each hit must expose ``.nrc``, ``.nrc_desc``, ``.response_hex``
    and the 16-bit key attribute named by ``key_attr`` (``rid`` or ``did``).
    """
    lines: list[str] = [f"  {section_name}:"]
    for hit in hits:
        lines.extend(_format_hit_entry(hit, key_attr))
    return lines


def _parse_existing_entries(section_body: str, key_width: int = 4) -> dict[str, list[str]]:
    """Extract existing DID/RID/LID entries from a section body as raw text blocks.

    Returns ``{KEY_HEX: [line1, line2, ...]}`` where each value is the raw
    lines of that entry (4-space-indented key line + 6-space-indented fields).
    This preserves any hand-edited notes or fields when merging.

    ``section_body`` is the text after the ``  <section_name>:`` header and
    before the next 0/2-space-indented key (not including those bookends).
    ``key_width`` is the number of hex digits in the entry key (4 for UDS DIDs,
    2 for KWP2000 local identifiers).
    """
    entries: dict[str, list[str]] = {}
    # Each entry starts with "    <HEX>:\n" at 4-space indent (key optionally quoted)
    entry_re = re.compile(
        r'^ {4}(["\']?)([0-9A-Fa-f]{' + str(key_width) + r"})\1:\s*$", re.MULTILINE
    )
    matches = list(entry_re.finditer(section_body))
    for i, m in enumerate(matches):
        key = m.group(2).upper()
        start = m.start()
        end = matches[i + 1].start() if i + 1 < len(matches) else len(section_body)
        block = section_body[start:end].rstrip("\n")
        entries[key] = block.split("\n")
    return entries


def _append_hit_block(
    ecu_name: str,
    hits,
    section_name: str,
    key_attr: str,
    pids_dir: Path | None = None,
    key_width: int = 4,
) -> Path:
    """Shared implementation for writing a scanner-generated YAML section.

    MERGE semantics: existing entries are preserved; entries in ``hits`` are
    upserted (overwrite on key conflict). Output is sorted by key ascending.
    This lets narrow/targeted scans run without wiping discoveries outside
    the scanned range.
    """
    if not hits:
        return find_ecu_file(ecu_name, pids_dir=pids_dir)

    fpath = find_ecu_file(ecu_name, pids_dir=pids_dir)
    text = fpath.read_text()
    ecu_start, ecu_end = _find_ecu_block(text, ecu_name)
    ecu_block = text[ecu_start:ecu_end]

    # Parse any pre-existing ``  <section_name>:`` section within the ECU block
    existing_entries: dict[str, list[str]] = {}
    sec = _keyed_block(ecu_block, section_name, 2, 0, len(ecu_block))
    if sec:
        section_body = ecu_block[sec[2] : sec[3]]
        existing_entries = _parse_existing_entries(section_body, key_width=key_width)
        # Strip the old section out; we'll reappend a merged one.
        ecu_block = _cut_block(ecu_block, sec[0], sec[3])

    # Upsert new hits into the entry map (new overrides old)
    merged: dict[str, list[str]] = dict(existing_entries)
    for hit in hits:
        key_hex = f"{getattr(hit, key_attr):0{key_width}X}"
        merged[key_hex] = _format_hit_entry(hit, key_attr, key_width=key_width)

    # Render sorted by key
    out_lines: list[str] = [f"  {section_name}:"]
    for key in sorted(merged.keys()):
        out_lines.extend(merged[key])
    new_section = "\n".join(out_lines) + "\n"

    body = ecu_block.rstrip("\n")
    new_ecu_block = body + "\n\n" + new_section

    new_text = text[:ecu_start] + new_ecu_block + text[ecu_end:]
    _write_text(fpath, new_text)
    _invalidate()
    return fpath


# ── Routines section field editor ────────────────────────────────────────────


def _find_routine_block(text: str, rid: str) -> tuple[int, int]:
    """Find the character range of a RID block inside ``routines:``.

    Returns (start, end) spanning from the RID key line (bare or quoted) up to
    (but not including) the next sibling / dedent, or the comments heading it.
    Raises ``PidsEditError`` if the RID isn't found under ``routines:``.
    """
    return _find_section_entry(text, "routines", rid, noun="RID")


def update_routines_field(
    ecu_name: str,
    rid: str,
    field: str,
    value: str | bool,
    pids_dir: Path | None = None,
) -> Path:
    """Update a single RID field in-place in the ``routines:`` section.

    Supports the same fields as ``update_iocontrol_field``: label, verified,
    notes. Returns the file path edited. Raises ``PidsEditError`` on failure.
    """
    if field not in EDITABLE_FIELDS:
        raise PidsEditError(f"Field {field!r} not editable; allowed: {EDITABLE_FIELDS}")

    fpath = find_ecu_file(ecu_name, pids_dir=pids_dir)
    text = fpath.read_text()
    start, end = _find_routine_block(text, rid)
    block = text[start:end]

    if field == "label":
        new_line = f"      label: {_format_label(str(value).strip())}"
        new_block = _replace_field_in_block(block, "label", new_line)
    elif field == "verified":
        new_line = f"      verified: {_format_verified(bool(value))}"
        new_block = _replace_field_in_block(block, "verified", new_line)
    elif field == "notes":
        new_lines = _format_block_scalar("      ", "notes", str(value))
        new_block = _replace_field_in_block(block, "notes", new_lines)
    else:  # pragma: no cover
        raise PidsEditError(field)

    if new_block == block:
        return fpath  # no-op

    new_text = text[:start] + new_block + text[end:]
    _write_text(fpath, new_text)
    _invalidate()
    return fpath


# ── Public API ───────────────────────────────────────────────────────────────


def update_iocontrol_field(
    ecu_name: str,
    did: str,
    field: str,
    value: str | bool,
    pids_dir: Path | None = None,
) -> Path:
    """Update a single DID field in-place and return the file path edited.

    ``field`` is one of :data:`IOCONTROL_FIELDS`; the field is added when the
    entry lacks it. Raises ``PidsEditError`` on any safety-relevant failure
    (ECU/DID not found, unsupported field, etc.).
    """
    if field not in IOCONTROL_FIELDS:
        raise PidsEditError(f"Field {field!r} not editable; allowed: {IOCONTROL_FIELDS}")

    fpath = find_ecu_file(ecu_name, pids_dir=pids_dir)
    text = fpath.read_text()
    start, end = _find_did_block(text, did)
    block = text[start:end]

    if field == "notes":
        new_lines = _format_block_scalar("      ", "notes", str(value))
        new_block = _replace_field_in_block(block, "notes", new_lines)
    else:
        if field in _IOCONTROL_BOOL_FIELDS:
            rendered = _format_verified(bool(value))
        elif field in _IOCONTROL_QUOTED_FIELDS:
            rendered = f'"{str(value).strip().upper()}"'
        else:
            rendered = _format_label(str(value).strip())
        new_block = _replace_field_in_block(block, field, f"      {field}: {rendered}")

    if new_block == block:
        return fpath  # no-op

    new_text = text[:start] + new_block + text[end:]
    _write_text(fpath, new_text)
    _invalidate()
    return fpath


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
    ecu_name: str,
    did: str,
    label: str,
    pids_dir: Path | None = None,
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

    fpath = find_ecu_file(ecu_name, pids_dir=pids_dir)
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
    _write_text(fpath, new_text)
    _invalidate()
    return fpath
