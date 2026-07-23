"""Surgical in-place editing of per-ECU PID YAML files.

These helpers update a single field (label / verified / notes) on a single
IOControl DID without rewriting the whole YAML file. This preserves comments,
anchors, block-scalar styles, and hand-curated ordering that a round-trip
through ``yaml.safe_dump`` would destroy.

Assumptions about file layout (all current ecus/*.yaml files conform):

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

import datetime
import re
from pathlib import Path


def _resolve_pids_dir(pids_dir: Path | None) -> Path:
    """Resolve the per-ECU definitions directory, defaulting to the active profile's."""
    if pids_dir is None:
        from .profile import active

        return active().ecus_dir
    return Path(pids_dir)


def _invalidate() -> None:
    """Drop the memoized ECU-definition load after a file write."""
    from .pids import clear_cache

    clear_cache()


# IOControl fields editable from the TUI.
EDITABLE_FIELDS = ("label", "verified", "notes")


class PidsEditError(Exception):
    """Raised when a DID or file cannot be located / edited safely."""


# ── File discovery ───────────────────────────────────────────────────────────


def find_ecu_file(ecu_name: str, pids_dir: Path | None = None) -> Path:
    """Return the pids/<ecu>.yaml file that defines ``ecu_name``.

    Scans every non-underscore YAML in ``pids_dir`` for a top-level
    ``<ecu_name>:`` key (0-space indent, case-insensitive on the name).
    """
    pids_dir = _resolve_pids_dir(pids_dir)
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
    raise PidsEditError(f"ECU {ecu_name!r} not found in any ecus/*.yaml")


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


def _yaml_reinterprets(value: str) -> bool:
    """True if YAML would parse the bare scalar as a non-string.

    Bare scalars like ``220101`` (int), ``1.5`` (float), ``true`` (bool),
    ``null`` or ``2026-04-15`` (date) round-trip to a non-``str`` type, which
    breaks downstream string comparisons (e.g. a numeric research ``target``).
    Such values must be quoted to stay strings.
    """
    import yaml

    try:
        return not isinstance(yaml.safe_load(value), str)
    except yaml.YAMLError:
        return True


def _format_label(value: str) -> str:
    """Render a label value as a YAML scalar line body."""
    value = value.strip()
    if not value:
        return '""'
    # Quote if it contains characters that are special at the start of a
    # YAML scalar, or a ': ' sequence, or leading/trailing whitespace, or if a
    # bare scalar would be re-parsed as a non-string (int/float/bool/null/date).
    needs_quote = (
        not value
        or value[0] in "!&*[]{}|>%@`\"'#,"
        or ": " in value
        or " #" in value
        or value != value.strip()
        or _yaml_reinterprets(value)
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
    existing_re = re.compile(r"^ {2}sessions:\s*$", re.MULTILINE)
    m = existing_re.search(ecu_block)
    if m:
        tail_re = re.compile(r"^ {0,2}[A-Za-z_]", re.MULTILINE)
        tail = tail_re.search(ecu_block, pos=m.end())
        sec_end = tail.start() if tail else len(ecu_block)
        section_body = ecu_block[m.end() : sec_end]
        entry_re = re.compile(r'^ {4}"?([0-9A-Fa-f]{1,2})"?:\s*$', re.MULTILINE)
        matches = list(entry_re.finditer(section_body))
        for i, em in enumerate(matches):
            key = em.group(1).upper().zfill(2)
            start = em.start()
            end = matches[i + 1].start() if i + 1 < len(matches) else len(section_body)
            block = section_body[start:end].rstrip("\n")
            existing_entries[key] = block.split("\n")
        # Strip the old section; a merged one is reappended below.
        ecu_block = ecu_block[: m.start()] + ecu_block[sec_end:]

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
    fpath.write_text(new_text)
    _invalidate()
    return fpath


def _format_hit_entry(hit, key_attr: str, key_width: int = 4) -> list[str]:
    """Render a single hit entry (4-space indent for key, 6 for fields).
    Narrow (KWP 2-digit) keys are quoted so all-digit local identifiers like
    ``30``/``80`` are not parsed as YAML integers/octal.
    """
    key_val = getattr(hit, key_attr)
    key_hex = f"{key_val:0{key_width}X}"
    key_token = f'"{key_hex}"' if key_width < 4 else key_hex
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
    entry_re = re.compile(r'^ {4}"?([0-9A-Fa-f]{' + str(key_width) + r'})"?:\s*$', re.MULTILINE)
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
    existing_re = re.compile(r"^ {2}" + re.escape(section_name) + r":\s*$", re.MULTILINE)
    m = existing_re.search(ecu_block)
    if m:
        tail_re = re.compile(r"^ {0,2}[A-Za-z_]", re.MULTILINE)
        tail = tail_re.search(ecu_block, pos=m.end())
        sec_end = tail.start() if tail else len(ecu_block)
        section_body = ecu_block[m.end() : sec_end]
        existing_entries = _parse_existing_entries(section_body, key_width=key_width)
        # Strip the old section out; we'll reappend a merged one.
        ecu_block = ecu_block[: m.start()] + ecu_block[sec_end:]

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
    fpath.write_text(new_text)
    _invalidate()
    return fpath


# ── Routines section field editor ────────────────────────────────────────────


def _find_routine_block(text: str, rid: str) -> tuple[int, int]:
    """Find the character range of a RID block inside ``routines:``.

    Returns (start, end) spanning from the RID key line up to (but not
    including) the next sibling / dedent. Raises ``PidsEditError`` if the
    RID isn't found under ``routines:``.
    """
    rid_u = rid.strip().upper()
    sec_re = re.compile(r"^ {2}routines:\s*$", re.MULTILINE)
    sec_m = sec_re.search(text)
    if not sec_m:
        raise PidsEditError("No routines: section found")

    # Section body ends at the next line at 2-or-less spaces of content.
    tail_re = re.compile(r"^ {0,2}[A-Za-z_]", re.MULTILINE)
    tail_m = tail_re.search(text, pos=sec_m.end() + 1)
    sec_end = tail_m.start() if tail_m else len(text)

    # Find "    RID:" within the section.
    rid_re = re.compile(rf"^( {{4}}){re.escape(rid_u)}:\s*$", re.MULTILINE)
    rm = rid_re.search(text, pos=sec_m.end(), endpos=sec_end)
    if not rm:
        raise PidsEditError(f"RID {rid_u!r} not found under routines:")

    start = rm.start()
    next_re = re.compile(r"^ {4}[A-Za-z0-9]", re.MULTILINE)
    nm = next_re.search(text, pos=rm.end(), endpos=sec_end)
    end = nm.start() if nm else sec_end
    return start, end


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
    _invalidate()
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
    return len(did_re.findall(text[sec_m.end() : sec_end]))


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
        while (
            insertion_point > cm.end()
            and without_disc[insertion_point - 1] == "\n"
            and insertion_point >= 2
            and without_disc[insertion_point - 2] == "\n"
        ):
            insertion_point -= 1
        new_block = without_disc[:insertion_point] + new_entry + without_disc[insertion_point:]
    else:
        # No iocontrol: section — create one at end of ECU block.
        body = without_disc.rstrip("\n")
        new_block = body + "\n\n  iocontrol:\n" + new_entry

    new_text = text[:ecu_start] + new_block + text[ecu_end:]
    fpath.write_text(new_text)
    _invalidate()
    return fpath


# ── Parameter + research editing (reverse-engineering workflow) ───────────────
#
# Unlike the scanner sections above (routines / iocontrol_discoveries), these
# edit the hand-authored pids: and research: structures used when decoding a new
# PID. The deeper nesting (pids: PID: parameters: PARAM: field) needs its own
# indent-anchored locators. Every write is followed by a YAML re-parse; on any
# failure the original text is restored so a botched edit never lands on disk.

# Canonical field order for a rendered parameter (matches pids/_schema.yaml).
PARAM_FIELD_ORDER = (
    "expression",
    "unit",
    "ha_class",
    "mqtt_topic",
    "min",
    "max",
    "source",
    "source_links",
    "verified",
    "notes",
    "enabled",
    "display",
)

# Canonical field order for a rendered research entry.
RESEARCH_FIELD_ORDER = (
    "type",
    "target",
    "status",
    "priority",
    "vehicle_states",
    "created",
    "updated",
    "date",
    "result",
    "notes",
    "sources",
    "what_to_test",
    "capture_protocol",
)


def _keyed_block(text: str, name: str, indent: int, win_start: int, win_end: int):
    """Locate ``<indent spaces><name>:`` within ``[win_start, win_end)``.

    Returns ``(hdr_start, line_end, body_start, body_end, inline)`` or ``None``:
      - ``hdr_start``  offset of the key line
      - ``line_end``   offset of the newline ending the key line
      - ``body_start`` offset of the first child line
      - ``body_end``   offset just before the next same-or-shallower sibling
      - ``inline``     any text after ``name:`` on the header line (e.g. ``{}``)
    """
    pat = re.compile(rf"^ {{{indent}}}{re.escape(name)}:[ \t]*(.*)$", re.MULTILINE)
    m = pat.search(text, win_start, win_end)
    if not m:
        return None
    inline = m.group(1).strip()
    line_end = text.find("\n", m.start())
    if line_end == -1:
        line_end = win_end
    body_start = min(line_end + 1, win_end)
    tail = re.compile(rf"^ {{0,{indent}}}[^\s#]", re.MULTILINE).search(text, body_start, win_end)
    body_end = tail.start() if tail else win_end
    return (m.start(), line_end, body_start, body_end, inline)


def _format_scalar_field(indent: str, key: str, value) -> str:
    """Render one ``key: value`` scalar line with schema-appropriate quoting."""
    if isinstance(value, bool):
        return f"{indent}{key}: {'true' if value else 'false'}"
    if key in ("min", "max"):
        return f'{indent}{key}: "{value}"'  # schema convention: quoted strings
    return f"{indent}{key}: {_format_label(str(value))}"


def _format_block_scalar(indent: str, key: str, value: str) -> list[str]:
    """Render ``key: >`` followed by the folded body, indented two deeper."""
    body_indent = indent + "  "
    out = [f"{indent}{key}: >"]
    for ln in str(value).strip("\n").splitlines() or [""]:
        out.append(f"{body_indent}{ln.rstrip()}")
    return out


def _format_list_field(indent: str, key: str, values) -> list[str]:
    """Render a block list ``key:`` / ``  - item`` (empty -> ``key: []``)."""
    if not values:
        return [f"{indent}{key}: []"]
    out = [f"{indent}{key}:"]
    for item in values:
        out.append(f"{indent}  - {_format_label(str(item))}")
    return out


def _format_param_block(name: str, fields: dict, indent: int = 8) -> list[str]:
    """Render a full ``PARAM_NAME:`` block (key at ``indent``, fields +2)."""
    ind = " " * indent
    fld = " " * (indent + 2)
    lines = [f"{ind}{name}:"]
    for key in PARAM_FIELD_ORDER:
        if key not in fields or fields[key] is None:
            continue
        val = fields[key]
        if key == "notes":
            lines.extend(_format_block_scalar(fld, key, str(val)))
        elif key == "source_links":
            lines.extend(
                _format_list_field(fld, key, val if isinstance(val, (list, tuple)) else [val])
            )
        else:
            lines.append(_format_scalar_field(fld, key, val))
    return lines


def _format_research_item(fields: dict, indent: int = 4) -> list[str]:
    """Render one ``- ...`` research list item (dash at ``indent``, fields +2)."""
    dash = " " * indent + "- "
    fld = " " * (indent + 2)
    lines: list[str] = []
    for key in RESEARCH_FIELD_ORDER:
        if key not in fields or fields[key] is None:
            continue
        val = fields[key]
        prefix = dash if not lines else fld  # first field sits on the dash line
        if key == "vehicle_states":
            joined = ", ".join(str(v) for v in val) if isinstance(val, (list, tuple)) else str(val)
            lines.append(f"{prefix}{key}: [{joined}]")
        elif key in ("notes", "result") and "\n" in str(val):
            block = _format_block_scalar(fld, key, str(val))
            block[0] = prefix + block[0][len(fld) :]
            lines.extend(block)
        elif key == "capture_protocol":
            block = _format_block_scalar(fld, key, str(val))
            block[0] = prefix + block[0][len(fld) :]
            lines.extend(block)
        elif key in ("sources", "what_to_test"):
            block = _format_list_field(fld, key, val if isinstance(val, (list, tuple)) else [val])
            block[0] = prefix + block[0][len(fld) :]
            lines.extend(block)
        else:
            line = _format_scalar_field(fld, key, val)
            lines.append(prefix + line[len(fld) :])
    return lines


def _reparse_or_raise(fpath: Path) -> dict:
    """Re-read the file as YAML; raise ``PidsEditError`` if it no longer parses."""
    import yaml

    try:
        data = yaml.safe_load(fpath.read_text())
    except yaml.YAMLError as e:
        raise PidsEditError(f"edit produced invalid YAML: {e}") from e
    if not isinstance(data, dict):
        raise PidsEditError("edit produced a non-mapping top-level document")
    return data


def _safe_write(fpath: Path, original: str, new_text: str, ecu: str, checker) -> None:
    """Write ``new_text``, re-parse, and run ``checker(data[ecu])``.

    Restores ``original`` and raises ``PidsEditError`` if the result is invalid
    YAML or ``checker`` fails — so a broken surgical edit never persists.
    """
    fpath.write_text(new_text)
    _invalidate()
    try:
        data = _reparse_or_raise(fpath)
        ecu_def = data.get(ecu)
        if not isinstance(ecu_def, dict):
            raise PidsEditError(f"ECU {ecu!r} missing after edit")
        checker(ecu_def)
    except PidsEditError:
        fpath.write_text(original)
        _invalidate()
        raise
    except Exception as e:  # pragma: no cover - defensive
        fpath.write_text(original)
        _invalidate()
        raise PidsEditError(f"edit failed post-check, reverted: {e}") from e


def upsert_parameter(
    ecu_name: str,
    pid: str,
    param_name: str,
    expression: str,
    *,
    unit: str | None = None,
    ha_class: str | None = None,
    mqtt_topic: str | None = None,
    min: str | None = None,
    max: str | None = None,
    source: str | None = None,
    source_links: list | None = None,
    verified: bool | None = None,
    notes: str | None = None,
    enabled: bool | None = None,
    display: str | None = None,
    pids_dir: Path | None = None,
) -> Path:
    """Add or update one parameter under ``ECU.pids.<PID>.parameters``.

    New parameters are rendered from the provided fields in canonical order.
    Existing parameters have only the *provided* fields replaced in place
    (other fields and formatting are preserved). Creates the ``PID`` block
    and/or ``parameters:`` map if missing. Requires the ECU to already exist
    with a ``pids:`` section.

    The write is verified by a YAML re-parse; on failure the file is restored.
    """
    if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", param_name or ""):
        raise PidsEditError(f"invalid parameter name {param_name!r}")
    if not (expression or "").strip():
        raise PidsEditError("expression must not be empty")

    provided = {
        "expression": expression,
        "unit": unit,
        "ha_class": ha_class,
        "mqtt_topic": mqtt_topic,
        "min": min,
        "max": max,
        "source": source,
        "source_links": source_links,
        "verified": verified,
        "notes": notes,
        "enabled": enabled,
        "display": display,
    }
    fields = {k: v for k, v in provided.items() if v is not None}

    fpath = find_ecu_file(ecu_name, pids_dir=pids_dir)
    original = fpath.read_text()
    ecu_key = ecu_name.strip().upper()
    pid_u = str(pid).strip().upper()

    def transform(text: str) -> str:
        ecu_start, ecu_end = _find_ecu_block(text, ecu_name)
        pids = _keyed_block(text, "pids", 2, ecu_start, ecu_end)
        if not pids:
            raise PidsEditError(f"ECU {ecu_name!r} has no pids: section")
        _, _, pids_body_start, pids_body_end, _ = pids

        pidb = _keyed_block(text, pid_u, 4, pids_body_start, pids_body_end)
        if not pidb:
            # New PID block appended to the pids: section.
            param_lines = _format_param_block(param_name, fields, indent=8)
            block = [f"    {pid_u}:", "      status: active", "      parameters:", *param_lines]
            return _insert_lines(text, pids_body_start, pids_body_end, block)

        _, _, pid_body_start, pid_body_end, _ = pidb
        params = _keyed_block(text, "parameters", 6, pid_body_start, pid_body_end)
        param_lines = _format_param_block(param_name, fields, indent=8)

        if not params:
            # PID exists but has no parameters: — add one after the PID header.
            block = ["      parameters:", *param_lines]
            return _insert_lines(text, pid_body_start, pid_body_start, block)

        p_hdr, p_line_end, p_body_start, p_body_end, p_inline = params
        if p_inline in ("{}", "{ }"):
            # Convert inline empty map to block form, then add the param.
            new_header = "      parameters:\n" + "".join(ln + "\n" for ln in param_lines)
            return text[:p_hdr] + new_header + text[p_line_end + 1 :]

        existing = _keyed_block(text, param_name, 8, p_body_start, p_body_end)
        if existing:
            # Update only the provided fields on the existing param block.
            e_start = existing[0]
            e_end = existing[3]
            block_text = text[e_start:e_end]
            for key in PARAM_FIELD_ORDER:
                if key not in fields:
                    continue
                if key == "notes":
                    repl = _format_block_scalar(" " * 10, "notes", str(fields[key]))
                elif key == "source_links":
                    v = fields[key]
                    repl = _format_list_field(
                        " " * 10, "source_links", v if isinstance(v, (list, tuple)) else [v]
                    )
                else:
                    repl = _replace_param_field_line(" " * 10, key, fields[key])
                block_text = _replace_field_in_block_at(block_text, key, repl, indent=10)
            return text[:e_start] + block_text + text[e_end:]

        # New param appended into the existing parameters: map.
        return _insert_lines(text, p_body_start, p_body_end, param_lines)

    def checker(ecu_def: dict) -> None:
        params = (ecu_def.get("pids", {}).get(pid_u) or {}).get("parameters") or {}
        # PID keys may be int (bare 2101) or str; normalize.
        if param_name not in params:
            pids_map = ecu_def.get("pids", {})
            match = next((v for k, v in pids_map.items() if str(k).upper() == pid_u), None)
            params = (match or {}).get("parameters") or {}
        if param_name not in params:
            raise PidsEditError(f"parameter {param_name!r} missing after edit")
        if params[param_name].get("expression") != expression:
            raise PidsEditError("expression mismatch after edit")

    new_text = transform(original)
    _safe_write(fpath, original, new_text, ecu_key, checker)
    return fpath


def _remove_field_line(block: str, field: str, indent: int) -> str:
    """Drop a scalar ``field:`` line at ``indent`` spaces from ``block``."""
    field_re = re.compile(rf"^ {{{indent}}}{re.escape(field)}:")
    return "".join(ln for ln in block.splitlines(keepends=True) if not field_re.match(ln))


def set_pid_status(ecu_name: str, pid: str, status: str, *, pids_dir: Path | None = None) -> Path:
    """Set a PID's ``status:`` — one of active/draft/static/ignored.

    ``status:`` is required and explicit on every PID, so the value is always
    written (as the first field under the PID header), including ``active``. The
    write is verified by a YAML re-parse; on failure the original file is restored.
    """
    from canlib.pids import PID_STATUSES

    status = str(status).strip().lower()
    if status not in PID_STATUSES:
        raise PidsEditError(f"status must be one of {PID_STATUSES}, got {status!r}")

    fpath = find_ecu_file(ecu_name, pids_dir=pids_dir)
    original = fpath.read_text()
    ecu_key = ecu_name.strip().upper()
    pid_u = str(pid).strip().upper()

    def transform(text: str) -> str:
        ecu_start, ecu_end = _find_ecu_block(text, ecu_name)
        pids = _keyed_block(text, "pids", 2, ecu_start, ecu_end)
        if not pids:
            raise PidsEditError(f"ECU {ecu_name!r} has no pids: section")
        _, _, pids_body_start, pids_body_end, _ = pids
        pidb = _keyed_block(text, pid_u, 4, pids_body_start, pids_body_end)
        if not pidb:
            raise PidsEditError(f"PID {pid_u!r} not found under {ecu_name!r}")
        p_hdr, _p_line_end, _p_body_start, p_body_end, _inline = pidb
        block_text = text[p_hdr:p_body_end]
        block_text = _remove_field_line(block_text, "status", indent=6)
        lines = block_text.splitlines(keepends=True)
        block_text = lines[0] + f"      status: {status}\n" + "".join(lines[1:])
        return text[:p_hdr] + block_text + text[p_body_end:]

    def checker(ecu_def: dict) -> None:
        pids_map = ecu_def.get("pids", {}) or {}
        pdef = next((v for k, v in pids_map.items() if str(k).upper() == pid_u), None)
        if pdef is None:
            raise PidsEditError(f"PID {pid_u!r} missing after edit")
        got = str((pdef or {}).get("status", "active")).lower()
        if got != status:
            raise PidsEditError(f"status mismatch after edit: {got!r} != {status!r}")

    new_text = transform(original)
    _safe_write(fpath, original, new_text, ecu_key, checker)
    return fpath


def _replace_param_field_line(indent: str, key: str, value) -> str:
    """One rendered scalar/bool line for a param field (used on existing blocks)."""
    return _format_scalar_field(indent, key, value)


def _insert_lines(text: str, region_start: int, region_end: int, lines: list[str]) -> str:
    """Insert ``lines`` at the end of ``[region_start, region_end)``.

    Backs up over trailing blank lines so the insertion sits adjacent to the
    last real content line rather than after a gap.
    """
    ins = region_end
    while ins > region_start and text[ins - 1] == "\n" and (ins < 2 or text[ins - 2] == "\n"):
        ins -= 1
    payload = "".join(ln + "\n" for ln in lines)
    return text[:ins] + payload + text[ins:]


def _replace_field_in_block_at(block: str, field: str, new_line_or_lines, indent: int) -> str:
    """Like ``_replace_field_in_block`` but for an arbitrary field indent.

    Replaces ``field:`` (and any block-scalar continuation) at ``indent`` spaces
    within ``block``; appends the field if absent.
    """
    replacement_lines = (
        [new_line_or_lines] if isinstance(new_line_or_lines, str) else list(new_line_or_lines)
    )
    lines = block.splitlines()
    out: list[str] = []
    i = 0
    replaced = False
    field_re = re.compile(rf"^ {{{indent}}}{re.escape(field)}:(.*)$")
    while i < len(lines):
        line = lines[i]
        m = field_re.match(line)
        if m and not replaced:
            rest = m.group(1).strip()
            i += 1
            if rest in (">", "|", ">-", "|-", ">+", "|+"):
                # Skip block-scalar continuation (indented deeper than field).
                while i < len(lines) and (
                    lines[i] == "" or lines[i].startswith(" " * (indent + 1))
                ):
                    if re.match(rf"^ {{{indent}}}[A-Za-z_]", lines[i]):
                        break
                    i += 1
            out.extend(replacement_lines)
            replaced = True
            continue
        out.append(line)
        i += 1
    if not replaced:
        while out and out[-1].strip() == "":
            out.pop()
        out.extend(replacement_lines)
    result = "\n".join(out)
    if block.endswith("\n") and not result.endswith("\n"):
        result += "\n"
    return result


def _today() -> str:
    """Today's date as ``YYYY-MM-DD`` (local time) for research timestamps."""
    return datetime.date.today().isoformat()


def add_research_entry(
    ecu_name: str,
    *,
    type: str,
    target: str,
    status: str,
    priority: str | None = None,
    vehicle_states: list | None = None,
    created: str | None = None,
    updated: str | None = None,
    date: str | None = None,
    result: str | None = None,
    notes: str | None = None,
    sources: list | None = None,
    what_to_test: list | None = None,
    capture_protocol: str | None = None,
    pids_dir: Path | None = None,
) -> Path:
    """Append a new item to the ECU's ``research:`` list (creating it if absent).

    ``created`` and ``updated`` default to today's date (``YYYY-MM-DD``) so every
    entry is timestamped without the caller having to pass anything.
    """
    today = _today()
    provided = {
        "type": type,
        "target": target,
        "status": status,
        "priority": priority,
        "vehicle_states": vehicle_states,
        "created": created or today,
        "updated": updated or today,
        "date": date,
        "result": result,
        "notes": notes,
        "sources": sources,
        "what_to_test": what_to_test,
        "capture_protocol": capture_protocol,
    }
    fields = {k: v for k, v in provided.items() if v is not None}
    for req in ("type", "target", "status"):
        if not str(fields.get(req, "")).strip():
            raise PidsEditError(f"research entry requires non-empty {req!r}")

    fpath = find_ecu_file(ecu_name, pids_dir=pids_dir)
    original = fpath.read_text()
    ecu_key = ecu_name.strip().upper()
    item_lines = _format_research_item(fields, indent=4)

    def transform(text: str) -> str:
        ecu_start, ecu_end = _find_ecu_block(text, ecu_name)
        research = _keyed_block(text, "research", 2, ecu_start, ecu_end)
        if research:
            _, _, r_body_start, r_body_end, _ = research
            return _insert_lines(text, r_body_start, r_body_end, item_lines)
        # No research: section — append one at the end of the ECU block.
        body = text[ecu_start:ecu_end].rstrip("\n")
        new_block = body + "\n\n  research:\n" + "".join(ln + "\n" for ln in item_lines)
        return text[:ecu_start] + new_block + text[ecu_end:]

    def checker(ecu_def: dict) -> None:
        research = ecu_def.get("research")
        if not isinstance(research, list) or not any(
            str(e.get("target")) == str(target) and e.get("type") == type for e in research
        ):
            raise PidsEditError("research entry missing after edit")

    new_text = transform(original)
    _safe_write(fpath, original, new_text, ecu_key, checker)
    return fpath


def set_research_status(
    ecu_name: str,
    target: str,
    status: str,
    *,
    type: str | None = None,
    pids_dir: Path | None = None,
) -> Path:
    """Update the ``status:`` of the research item matching ``target`` (and ``type``).

    Also refreshes the item's ``updated`` timestamp to today's date so status
    transitions are dated automatically.

    Raises ``PidsEditError`` if no matching item is found or the match is
    ambiguous (multiple items share the target and no ``type`` was given).
    """
    fpath = find_ecu_file(ecu_name, pids_dir=pids_dir)
    original = fpath.read_text()
    ecu_key = ecu_name.strip().upper()
    target_norm = str(target).strip().strip('"').strip("'")
    today = _today()

    def transform(text: str) -> str:
        ecu_start, ecu_end = _find_ecu_block(text, ecu_name)
        research = _keyed_block(text, "research", 2, ecu_start, ecu_end)
        if not research:
            raise PidsEditError(f"ECU {ecu_name!r} has no research: section")
        _, _, r_body_start, r_body_end, _ = research
        body = text[r_body_start:r_body_end]

        item_re = re.compile(r"^ {4}- ", re.MULTILINE)
        starts = [r_body_start + m.start() for m in item_re.finditer(body)]
        target_re = re.compile(r"^ {4,6}(?:- )?target:[ \t]*(.*)$", re.MULTILINE)
        type_re = re.compile(r"^ {4,6}(?:- )?type:[ \t]*(.*)$", re.MULTILINE)

        matches = []
        for idx, s in enumerate(starts):
            e = starts[idx + 1] if idx + 1 < len(starts) else r_body_end
            item = text[s:e]
            tm = target_re.search(item)
            if not tm:
                continue
            item_target = tm.group(1).strip().strip('"').strip("'")
            if item_target != target_norm:
                continue
            if type is not None:
                ty = type_re.search(item)
                if not ty or ty.group(1).strip().strip('"').strip("'") != type:
                    continue
            matches.append((s, e))

        if not matches:
            raise PidsEditError(
                f"no research item with target {target!r}" + (f" and type {type!r}" if type else "")
            )
        if len(matches) > 1:
            raise PidsEditError(f"ambiguous target {target!r} ({len(matches)} matches); pass type=")

        s, e = matches[0]
        item = text[s:e]
        new_item = _replace_field_in_block_at(item, "status", f"      status: {status}", indent=6)
        new_item = _replace_field_in_block_at(
            new_item, "updated", f'      updated: "{today}"', indent=6
        )
        return text[:s] + new_item + text[e:]

    def checker(ecu_def: dict) -> None:
        research = ecu_def.get("research") or []
        ok = any(
            e.get("target") == target_norm
            and e.get("status") == status
            and e.get("updated") == today
            and (type is None or e.get("type") == type)
            for e in research
        )
        if not ok:
            raise PidsEditError("status not applied after edit")

    new_text = transform(original)
    _safe_write(fpath, original, new_text, ecu_key, checker)
    return fpath
