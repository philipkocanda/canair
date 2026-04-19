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
