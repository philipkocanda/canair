"""Surgical in-place editing of per-ECU PID YAML files.

These helpers update a single field (label / verified / notes) on a single
IOControl DID without rewriting the whole YAML file. This preserves comments,
anchors, block-scalar styles, and hand-curated ordering that a round-trip
through ``yaml.safe_dump`` would destroy.

Assumptions about file layout (all current pids/*.yaml files conform):

    ECU:                           <- 0-space indent
      tx_id: 0x770
      iocontrol:                   <- 2-space indent
        DID:                       <- 4-space indent
          label: "..."             <- 6-space indent
          verified: true
          notes: "..."
          notes: >                 <- block-scalar form
            multi-line text        <- 8+-space indent

The matchers below are intentionally anchored on indentation to avoid
accidentally editing similarly-named keys elsewhere in the file.
"""

from __future__ import annotations

import re
from pathlib import Path

from .constants import PIDS_DIR

# IOControl fields editable from the TUI.
EDITABLE_FIELDS = ("label", "verified", "notes")


class PidsEditError(Exception):
    """Raised when a DID or file cannot be located / edited safely."""


# ── File discovery ───────────────────────────────────────────────────────────


def find_ecu_file(ecu_name: str, pids_dir: Path = PIDS_DIR) -> Path:
    """Return the pids/<ecu>.yaml file that defines ``ecu_name``.

    Scans every non-underscore YAML in ``pids_dir`` for a top-level
    ``<ecu_name>:`` key (0-space indent, case-insensitive on the name).
    """
    pids_dir = Path(pids_dir)
    target = ecu_name.strip().upper()
    # Match a top-level key like "IGPM:" — no leading whitespace.
    pattern = re.compile(r"^([A-Za-z][A-Za-z0-9_\-]*):\s*$", re.MULTILINE)
    for fpath in sorted(pids_dir.glob("*.yaml")):
        if fpath.name.startswith("_"):
            continue
        text = fpath.read_text()
        for m in pattern.finditer(text):
            if m.group(1).upper() == target:
                return fpath
    raise PidsEditError(f"ECU {ecu_name!r} not found in any pids/*.yaml")


# ── DID block location ───────────────────────────────────────────────────────


def _find_did_block(text: str, did: str) -> tuple[int, int]:
    """Find the character range of a DID block in the file.

    Returns (start, end) offsets spanning from the DID key line up to (but
    not including) the next sibling line at the same or lower indent.
    """
    did_u = did.strip().upper()
    # Match "    DID:" where the indent must be exactly 4 spaces (DID lives
    # under ECU.iocontrol at depth 2 blocks in).
    start_re = re.compile(rf"^( {{4}}){re.escape(did_u)}:\s*$", re.MULTILINE)
    m = start_re.search(text)
    if not m:
        raise PidsEditError(f"DID {did_u!r} not found (expected 4-space indent)")

    start = m.start()
    # Find end: next line that starts with <=4 spaces of content (sibling DID
    # at same depth, or a new parent key at shallower indent). Blank lines
    # don't count as boundaries.
    end_re = re.compile(r"^( {0,4})[^\s#]", re.MULTILINE)
    for m2 in end_re.finditer(text, pos=m.end()):
        return start, m2.start()
    return start, len(text)


# ── Field reads (for diffing / initial values) ───────────────────────────────


_FIELD_RE_CACHE: dict[str, re.Pattern] = {}


def _field_line_re(field: str) -> re.Pattern:
    """Regex that matches ``      field: <value>`` within a DID block."""
    if field not in _FIELD_RE_CACHE:
        _FIELD_RE_CACHE[field] = re.compile(
            rf"^( {{6}}){re.escape(field)}:[ \t]*(.*)$",
            re.MULTILINE,
        )
    return _FIELD_RE_CACHE[field]


# ── Value formatting ─────────────────────────────────────────────────────────


def _format_label(value: str) -> str:
    """Render a label value as a YAML scalar line body."""
    value = value.strip()
    if not value:
        return '""'
    # Quote if it contains characters that are special at the start of a
    # YAML scalar, or a ': ' sequence, or leading/trailing whitespace.
    needs_quote = (
        not value
        or value[0] in "!&*[]{}|>%@`\"'#,"
        or ": " in value
        or " #" in value
        or value != value.strip()
    )
    if needs_quote:
        return '"' + value.replace("\\", "\\\\").replace('"', '\\"') + '"'
    return value


def _format_verified(value: bool) -> str:
    return "true" if value else "false"


def _format_notes_block(value: str, indent: str = "        ") -> list[str]:
    """Render notes as a block-scalar: ``notes: >`` followed by indented lines.

    Always uses block-scalar form so multi-line notes are preserved. Single-
    line notes get one indented continuation line, which YAML accepts.
    """
    lines = value.strip("\n").splitlines() or [""]
    out = ["      notes: >"]
    for ln in lines:
        out.append(f"{indent}{ln.rstrip()}")
    return out


# ── Block mutation ───────────────────────────────────────────────────────────


def _replace_field_in_block(block: str, field: str, new_line_or_lines: str | list[str]) -> str:
    """Return ``block`` with ``field:`` replaced, or the field added if missing.

    ``new_line_or_lines`` is either a complete replacement line (no trailing
    newline) or a list of full lines for multi-line values (notes).
    """
    if isinstance(new_line_or_lines, str):
        replacement = new_line_or_lines
        replacement_lines = [replacement]
    else:
        replacement_lines = new_line_or_lines
        replacement = "\n".join(replacement_lines)

    lines = block.splitlines()
    out: list[str] = []
    i = 0
    replaced = False
    while i < len(lines):
        line = lines[i]
        m = re.match(rf"^( {{6}}){re.escape(field)}:(.*)$", line)
        if m and not replaced:
            # Skip this line + any continuation lines (for block scalars the
            # continuation is indented deeper than 6 spaces).
            rest = m.group(2)
            is_block = rest.strip() in (">", "|", ">-", "|-", ">+", "|+")
            i += 1
            if is_block:
                while i < len(lines) and (lines[i] == "" or lines[i].startswith("       ")):
                    # Stop when we hit a sibling field at 6-space indent.
                    if re.match(r"^ {6}[A-Za-z_]", lines[i]):
                        break
                    i += 1
            out.extend(replacement_lines)
            replaced = True
            continue
        out.append(line)
        i += 1

    if not replaced:
        # Append before the trailing blank lines of the block, if any.
        # Find position just after the DID header line (first line).
        # Simplest: append at end (strip trailing blanks), then re-add one.
        while out and out[-1].strip() == "":
            out.pop()
        out.extend(replacement_lines)

    # Preserve exact trailing whitespace (splitlines() + "\n".join drops the
    # final newline/blank-line structure otherwise).
    trailing = ""
    i = len(block)
    while i > 0 and block[i - 1] == "\n":
        trailing += "\n"
        i -= 1
    result = "\n".join(out)
    if not result.endswith("\n") and trailing:
        result += trailing
    elif trailing and not result.endswith(trailing):
        # Top up to match original trailing newlines.
        stripped = result.rstrip("\n")
        result = stripped + trailing
    return result


# ── Routines section appender ────────────────────────────────────────────────


def _format_routines_block(hits) -> list[str]:
    """Deprecated: use ``_format_hit_block(hits, 'routines', 'rid')`` instead.

    Kept as a thin shim for backward compatibility with any external callers.
    """
    return _format_hit_block(hits, "routines", "rid")


def _find_ecu_block(text: str, ecu_name: str) -> tuple[int, int]:
    """Return (start, end) of the ECU's top-level block in ``text``.

    start = offset of the ``ECU:`` line.
    end   = offset just before the next top-level sibling (or EOF).
    """
    target = ecu_name.strip().upper()
    # Top-level ECU key — 0-space indent
    start_re = re.compile(r"^([A-Za-z][A-Za-z0-9_\-]*):\s*$", re.MULTILINE)
    ecu_start = None
    for m in start_re.finditer(text):
        if m.group(1).upper() == target:
            ecu_start = m.start()
            break
    if ecu_start is None:
        raise PidsEditError(f"ECU {ecu_name!r} not found at top level")

    # Next top-level sibling (not indented, not a comment-only line)
    # Start the search after the ECU's header line, not mid-token.
    header_end = text.find("\n", ecu_start)
    if header_end == -1:
        return ecu_start, len(text)
    search_from = header_end + 1
    after = text[search_from:]
    sibling_re = re.compile(r"^[A-Za-z][A-Za-z0-9_\-]*:\s*$", re.MULTILINE)
    m2 = sibling_re.search(after)
    if m2:
        return ecu_start, search_from + m2.start()
    return ecu_start, len(text)


def append_routines_block(ecu_name: str, hits, pids_dir: Path = PIDS_DIR) -> Path:
    """Write/overwrite a ``routines:`` section at the end of the ECU block.

    If a ``routines:`` section already exists for this ECU, it is replaced
    wholesale. Preserves surrounding YAML (pids/iocontrol/research blocks).

    Returns the file path edited. No-op if ``hits`` is empty.
    """
    return _append_hit_block(
        ecu_name=ecu_name,
        hits=hits,
        section_name="routines",
        key_attr="rid",
        pids_dir=pids_dir,
    )


def append_iocontrol_discoveries_block(
    ecu_name: str, hits, pids_dir: Path = PIDS_DIR
) -> Path:
    """Write/overwrite an ``iocontrol_discoveries:`` section at the end of
    the ECU block.

    Kept distinct from the curated ``iocontrol:`` block so the 0x2F DID
    scanner can rerun without clobbering human-authored on/off/notes entries.
    Promotion from a discovery to a fully-fledged iocontrol entry is a
    manual, per-DID step.

    Returns the file path edited. No-op if ``hits`` is empty.
    """
    return _append_hit_block(
        ecu_name=ecu_name,
        hits=hits,
        section_name="iocontrol_discoveries",
        key_attr="did",
        pids_dir=pids_dir,
    )


def _format_hit_entry(hit, key_attr: str) -> list[str]:
    """Render a single hit entry (4-space indent for key, 6 for fields)."""
    key_val = getattr(hit, key_attr)
    key_hex = f"{key_val:04X}"
    lines = [f"    {key_hex}:", f"      session: {hit.session}"]
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

    Each hit must expose ``.session``, ``.nrc``, ``.nrc_desc``, ``.response_hex``
    and the 16-bit key attribute named by ``key_attr`` (``rid`` or ``did``).
    """
    lines: list[str] = [f"  {section_name}:"]
    for hit in hits:
        lines.extend(_format_hit_entry(hit, key_attr))
    return lines


def _parse_existing_entries(section_body: str) -> dict[str, list[str]]:
    """Extract existing DID/RID entries from a section body as raw text blocks.

    Returns ``{KEY_HEX: [line1, line2, ...]}`` where each value is the raw
    lines of that entry (4-space-indented key line + 6-space-indented fields).
    This preserves any hand-edited notes or fields when merging.

    ``section_body`` is the text after the ``  <section_name>:`` header and
    before the next 0/2-space-indented key (not including those bookends).
    """
    entries: dict[str, list[str]] = {}
    # Each entry starts with "    <HEX>:\n" at 4-space indent
    entry_re = re.compile(r"^ {4}([0-9A-Fa-f]{4}):\s*$", re.MULTILINE)
    matches = list(entry_re.finditer(section_body))
    for i, m in enumerate(matches):
        key = m.group(1).upper()
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
    pids_dir: Path,
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
    existing_re = re.compile(
        r"^ {2}" + re.escape(section_name) + r":\s*$", re.MULTILINE
    )
    m = existing_re.search(ecu_block)
    if m:
        tail_re = re.compile(r"^ {0,2}[A-Za-z_]", re.MULTILINE)
        tail = tail_re.search(ecu_block, pos=m.end())
        sec_end = tail.start() if tail else len(ecu_block)
        section_body = ecu_block[m.end():sec_end]
        existing_entries = _parse_existing_entries(section_body)
        # Strip the old section out; we'll reappend a merged one.
        ecu_block = ecu_block[: m.start()] + ecu_block[sec_end:]

    # Upsert new hits into the entry map (new overrides old)
    merged: dict[str, list[str]] = dict(existing_entries)
    for hit in hits:
        key_hex = f"{getattr(hit, key_attr):04X}"
        merged[key_hex] = _format_hit_entry(hit, key_attr)

    # Render sorted by key
    out_lines: list[str] = [f"  {section_name}:"]
    for key in sorted(merged.keys()):
        out_lines.extend(merged[key])
    new_section = "\n".join(out_lines) + "\n"

    body = ecu_block.rstrip("\n")
    new_ecu_block = body + "\n\n" + new_section

    new_text = text[:ecu_start] + new_ecu_block + text[ecu_end:]
    fpath.write_text(new_text)
    return fpath


# ── Public API ───────────────────────────────────────────────────────────────


def update_iocontrol_field(
    ecu_name: str,
    did: str,
    field: str,
    value: str | bool,
    pids_dir: Path = PIDS_DIR,
) -> Path:
    """Update a single DID field in-place and return the file path edited.

    Raises ``PidsEditError`` on any safety-relevant failure (ECU/DID not
    found, unsupported field, etc.).
    """
    if field not in EDITABLE_FIELDS:
        raise PidsEditError(f"Field {field!r} not editable; allowed: {EDITABLE_FIELDS}")

    fpath = find_ecu_file(ecu_name, pids_dir=pids_dir)
    text = fpath.read_text()
    start, end = _find_did_block(text, did)
    block = text[start:end]

    if field == "label":
        new_line = f"      label: {_format_label(str(value))}"
        new_block = _replace_field_in_block(block, "label", new_line)
    elif field == "verified":
        new_line = f"      verified: {_format_verified(bool(value))}"
        new_block = _replace_field_in_block(block, "verified", new_line)
    elif field == "notes":
        new_lines = _format_notes_block(str(value))
        new_block = _replace_field_in_block(block, "notes", new_lines)
    else:  # pragma: no cover
        raise PidsEditError(field)

    if new_block == block:
        return fpath  # no-op

    new_text = text[:start] + new_block + text[end:]
    fpath.write_text(new_text)
    return fpath


# ── Discovery promotion ──────────────────────────────────────────────────────


def _find_discovery_block(text: str, did: str) -> tuple[int, int]:
    """Find the character range of a DID entry inside ``iocontrol_discoveries:``.

    Returns (start, end) spanning from the DID key line up to (but not
    including) the next sibling / dedent. Raises ``PidsEditError`` if the
    DID isn't found under ``iocontrol_discoveries:``.
    """
    did_u = did.strip().upper()
    # The discoveries section opens with "  iocontrol_discoveries:" (2-space
    # indent); its DID children are at 4-space indent.
    sec_re = re.compile(r"^ {2}iocontrol_discoveries:\s*$", re.MULTILINE)
    sec_m = sec_re.search(text)
    if not sec_m:
        raise PidsEditError("No iocontrol_discoveries: section found")

    # Section body ends at the next line at 2-or-less spaces of content (a
    # sibling key under the ECU) or at EOF.
    tail_re = re.compile(r"^ {0,2}[A-Za-z_]", re.MULTILINE)
    tail_m = tail_re.search(text, pos=sec_m.end() + 1)
    sec_end = tail_m.start() if tail_m else len(text)

    # Now find "    DID:" within [sec_m.end(), sec_end).
    did_re = re.compile(rf"^( {{4}}){re.escape(did_u)}:\s*$", re.MULTILINE)
    dm = did_re.search(text, pos=sec_m.end(), endpos=sec_end)
    if not dm:
        raise PidsEditError(f"DID {did_u!r} not found under iocontrol_discoveries:")

    start = dm.start()
    # End: next 4-space-indented DID key, or end of section.
    next_re = re.compile(r"^ {4}[A-Za-z0-9]", re.MULTILINE)
    nm = next_re.search(text, pos=dm.end(), endpos=sec_end)
    end = nm.start() if nm else sec_end
    return start, end


def _count_discovery_entries(text: str) -> int:
    """Count DID entries remaining in the iocontrol_discoveries section."""
    sec_re = re.compile(r"^ {2}iocontrol_discoveries:\s*$", re.MULTILINE)
    sec_m = sec_re.search(text)
    if not sec_m:
        return 0
    tail_re = re.compile(r"^ {0,2}[A-Za-z_]", re.MULTILINE)
    tail_m = tail_re.search(text, pos=sec_m.end() + 1)
    sec_end = tail_m.start() if tail_m else len(text)
    did_re = re.compile(r"^ {4}[A-Za-z0-9]+:\s*$", re.MULTILINE)
    return len(did_re.findall(text[sec_m.end():sec_end]))


def _remove_discovery_section(text: str) -> str:
    """Drop the entire ``iocontrol_discoveries:`` block (header + body)."""
    sec_re = re.compile(r"^ {2}iocontrol_discoveries:\s*$", re.MULTILINE)
    sec_m = sec_re.search(text)
    if not sec_m:
        return text
    tail_re = re.compile(r"^ {0,2}[A-Za-z_]", re.MULTILINE)
    tail_m = tail_re.search(text, pos=sec_m.end() + 1)
    sec_end = tail_m.start() if tail_m else len(text)
    # Eat trailing blank lines before the section so we don't leave a gap.
    start = sec_m.start()
    while start > 0 and text[start - 1] == "\n" and (start < 2 or text[start - 2] == "\n"):
        start -= 1
    return text[:start] + text[sec_end:]


def _infer_on_payload(discovery_block: str) -> str:
    """Infer the ``on`` payload hex from a discovery's captured response.

    The 0x2F positive response is ``6F {DID_hi} {DID_lo} {controlStateRecord...}``
    so the trailing bytes after the 3-byte echo *are* the controlStateRecord
    the ECU reported as current state. Replaying that record back as a
    shortTermAdjustment payload (``2F{DID}03{tail}``) is a guaranteed
    in-range, safe probe — it asserts "make state == current state" — and
    avoids NRC 0x31 requestOutOfRange rejections that the previous
    ``FF × nbytes`` default triggered.

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
    pids_dir: Path = PIDS_DIR,
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
    if not re.fullmatch(r"[0-9A-F]{4}", did_u):
        raise PidsEditError(f"DID must be 4 hex digits, got {did!r}")
    label = label.strip()
    if not label:
        raise PidsEditError("label must not be empty")

    fpath = find_ecu_file(ecu_name, pids_dir=pids_dir)
    text = fpath.read_text()
    ecu_start, ecu_end = _find_ecu_block(text, ecu_name)
    ecu_block = text[ecu_start:ecu_end]

    # Guard against clobbering an existing curated entry.
    curated_re = re.compile(r"^ {2}iocontrol:\s*$", re.MULTILINE)
    cm = curated_re.search(ecu_block)
    if cm:
        # Search for DID only within the iocontrol: section (up to next
        # 2-space-indent sibling).
        tail_re = re.compile(r"^ {0,2}[A-Za-z_]", re.MULTILINE)
        tm = tail_re.search(ecu_block, pos=cm.end() + 1)
        c_end = tm.start() if tm else len(ecu_block)
        dup_re = re.compile(rf"^ {{4}}{re.escape(did_u)}:\s*$", re.MULTILINE)
        if dup_re.search(ecu_block, pos=cm.end(), endpos=c_end):
            raise PidsEditError(
                f"DID {did_u} already exists under curated iocontrol: — cannot promote"
            )

    # Find the discovery block and infer state-byte length from its response.
    rel_text = ecu_block
    d_start, d_end = _find_discovery_block(rel_text, did_u)
    discovery_block = rel_text[d_start:d_end]
    on_payload = _infer_on_payload(discovery_block)
    without_disc = rel_text[:d_start] + rel_text[d_end:]

    # If the discoveries section is now empty, remove it entirely.
    if _count_discovery_entries(without_disc) == 0:
        without_disc = _remove_discovery_section(without_disc)

    # Build the new curated entry (4-space indent inside "  iocontrol:").
    new_entry = (
        f"    {did_u}:\n"
        f"      label: {_format_label(label)}\n"
        f"      verified: false\n"
        f'      on: "2F{did_u}03{on_payload}"\n'
        f'      off: "2F{did_u}00"\n'
    )

    # Insert the new entry into the iocontrol: section. If missing, create it
    # just before iocontrol_discoveries: / research: / end-of-ECU.
    if cm:
        # Append at end of iocontrol: section.
        tail_re = re.compile(r"^ {0,2}[A-Za-z_]", re.MULTILINE)
        tm = tail_re.search(without_disc, pos=cm.end() + 1)
        c_end = tm.start() if tm else len(without_disc)
        # Insert before c_end, preserving any trailing blank line.
        insertion_point = c_end
        # Back up over trailing blank lines so entry sits adjacent to siblings.
        while insertion_point > cm.end() and without_disc[insertion_point - 1] == "\n" \
                and insertion_point >= 2 and without_disc[insertion_point - 2] == "\n":
            insertion_point -= 1
        new_block = (
            without_disc[:insertion_point]
            + new_entry
            + without_disc[insertion_point:]
        )
    else:
        # No iocontrol: section — create one at end of ECU block.
        body = without_disc.rstrip("\n")
        new_block = body + "\n\n  iocontrol:\n" + new_entry

    new_text = text[:ecu_start] + new_block + text[ecu_end:]
    fpath.write_text(new_text)
    return fpath
