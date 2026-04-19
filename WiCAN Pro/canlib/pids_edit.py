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


def _format_hit_block(hits, section_name: str, key_attr: str) -> list[str]:
    """Render a hit-block (routines or iocontrol_discoveries).

    Each hit must expose ``.session``, ``.nrc``, ``.nrc_desc``, ``.response_hex``
    and the 16-bit key attribute named by ``key_attr`` (``rid`` or ``did``).
    """
    lines: list[str] = [f"  {section_name}:"]
    for hit in hits:
        key_val = getattr(hit, key_attr)
        key_hex = f"{key_val:04X}"
        lines.append(f"    {key_hex}:")
        lines.append(f"      session: {hit.session}")
        if hit.nrc is None:
            resp = hit.response_hex or ""
            lines.append(f'      response: "{resp}"')
        else:
            lines.append(f"      nrc: 0x{hit.nrc:02X}")
            desc = (hit.nrc_desc or "").replace('"', '\\"')
            lines.append(f'      nrc_desc: "{desc}"')
        lines.append('      notes: ""')
    return lines


def _append_hit_block(
    ecu_name: str,
    hits,
    section_name: str,
    key_attr: str,
    pids_dir: Path,
) -> Path:
    """Shared implementation for writing a scanner-generated YAML section."""
    if not hits:
        return find_ecu_file(ecu_name, pids_dir=pids_dir)

    fpath = find_ecu_file(ecu_name, pids_dir=pids_dir)
    text = fpath.read_text()
    ecu_start, ecu_end = _find_ecu_block(text, ecu_name)
    ecu_block = text[ecu_start:ecu_end]

    new_lines = _format_hit_block(hits, section_name, key_attr)
    new_section = "\n".join(new_lines) + "\n"

    # Remove any pre-existing ``  <section_name>:`` section within the ECU block
    existing_re = re.compile(
        r"^ {2}" + re.escape(section_name) + r":\s*$", re.MULTILINE
    )
    m = existing_re.search(ecu_block)
    if m:
        tail_re = re.compile(r"^ {0,2}[A-Za-z_]", re.MULTILINE)
        tail = tail_re.search(ecu_block, pos=m.end())
        sec_end = tail.start() if tail else len(ecu_block)
        ecu_block = ecu_block[: m.start()] + ecu_block[sec_end:]

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
