"""Shared primitives for surgical in-place editing of per-ECU PID YAML files.

These helpers locate ECU/DID/param blocks and mutate a single field without
rewriting the whole file — preserving comments, anchors, block-scalar styles,
and hand-curated ordering that a ``yaml.safe_dump`` round-trip would destroy.
The domain editors (``hits`` for scanner sections, ``params`` for the
hand-authored pids:/research:/identity: structures) build on these.

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
from collections.abc import Sequence
from pathlib import Path


def _resolve_pids_dir(pids_dir: Path | None) -> Path:
    """Resolve the per-ECU definitions directory, defaulting to the active profile's."""
    if pids_dir is None:
        from ..profile import active

        return active().ecus_dir
    return Path(pids_dir)


def _invalidate() -> None:
    """Drop the memoized ECU-definition load after a file write."""
    from ..pids import clear_cache

    clear_cache()


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


def _keyed_block(text: str, name: str, indent: int, win_start: int, win_end: int):
    """Locate ``<indent spaces><name>:`` within ``[win_start, win_end)``.

    The key may be bare or quoted (``0105:`` / ``"0105":`` / ``'0105':``) — a PID
    code that YAML would otherwise coerce (e.g. a leading-zero decimal like
    ``0105``) is written quoted by :func:`_key_token`, so the finder must match
    either form.

    Returns ``(hdr_start, line_end, body_start, body_end, inline)`` or ``None``:
      - ``hdr_start``  offset of the key line
      - ``line_end``   offset of the newline ending the key line
      - ``body_start`` offset of the first child line
      - ``body_end``   offset just before the next same-or-shallower sibling
      - ``inline``     any text after ``name:`` on the header line (e.g. ``{}``)
    """
    q = re.escape(name)
    pat = re.compile(rf'^ {{{indent}}}(?:"{q}"|\'{q}\'|{q}):[ \t]*(.*)$', re.MULTILINE)
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


def _needs_key_quoting(key: str) -> bool:
    """True if a bare YAML scalar ``key`` wouldn't stringify back to ``key``.

    PID codes are hex strings, but a code of all decimal digits with a leading
    zero (``0105``, ``0902`` — standard OBD-II Mode-01/09 PIDs) is parsed by YAML
    as an *integer*, losing the leading zero (``0105`` -> ``105``). The surgical
    editors compare keys via ``str(k)``, so such a key silently fails to round-
    trip and the edit reverts — unless it's quoted. Keys that already round-trip
    via ``str()`` (``2101`` -> ``2101``) or are non-numeric (``22B004``, ``010C``)
    are left unquoted, so existing profiles don't churn.
    """
    import yaml

    try:
        v = yaml.safe_load(key)
    except yaml.YAMLError:
        return True
    return str(v) != key


def _key_token(key: str) -> str:
    """Render a mapping key, double-quoting it only when a bare token wouldn't
    round-trip (see :func:`_needs_key_quoting`)."""
    return f'"{key}"' if _needs_key_quoting(key) else key


def _format_scalar_field(indent: str, key: str, value) -> str:
    """Render one ``key: value`` scalar line with schema-appropriate quoting."""
    if isinstance(value, bool):
        return f"{indent}{key}: {'true' if value else 'false'}"
    if key in ("min", "max"):
        return f'{indent}{key}: "{value}"'  # schema convention: quoted strings
    return f"{indent}{key}: {_format_label(str(value))}"


def _format_block_scalar(indent: str, key: str, value: str) -> list[str]:
    """Render a free-text field per the shared note policy (canlib.yaml_rt).

    A short single-line value stays an inline scalar (``key: value``); a longer
    or multi-line value becomes a folded ``key: >-`` block, word-wrapped at
    ~``NOTE_WRAP_WIDTH`` columns (blank lines preserved as paragraph breaks).
    Folding is value-preserving, so the note round-trips to the original text.
    """
    from ..yaml_rt import NOTE_WRAP_WIDTH, note_should_inline, wrap_note_lines

    value = str(value)
    if note_should_inline(value, len(indent) + len(key) + len(": ")):
        inline = _format_scalar_field(indent, key, value)
        # _format_scalar_field may add quotes; keep inline only if it still fits.
        if len(inline) <= NOTE_WRAP_WIDTH:
            return [inline]
    body_indent = indent + "  "
    width = max(20, NOTE_WRAP_WIDTH - len(body_indent))
    out = [f"{indent}{key}: >-"]
    out.extend(f"{body_indent}{piece}" if piece else "" for piece in wrap_note_lines(value, width))
    return out


def _format_list_field(indent: str, key: str, values: Sequence[object]) -> list[str]:
    """Render a block list ``key:`` / ``  - item`` (empty -> ``key: []``)."""
    if not values:
        return [f"{indent}{key}: []"]
    out = [f"{indent}{key}:"]
    for item in values:
        out.append(f"{indent}  - {_format_label(str(item))}")
    return out


def _format_inline_list_field(indent: str, key: str, values: Sequence[object]) -> list[str]:
    """Render a flow (inline) list ``key: [a, b, c]`` (empty -> ``key: []``).

    Used for short, hand-curated code lists (e.g. ``can_bus: [B-CAN, P-CAN]``)
    where the inline form reads better than a multi-line block list. Items are
    quoted only when a bare scalar would be unsafe (via ``_format_label``).
    """
    if not values:
        return [f"{indent}{key}: []"]
    items = ", ".join(_format_label(str(v)) for v in values)
    return [f"{indent}{key}: [{items}]"]


def _format_map_field(indent: str, key: str, mapping: dict) -> list[str]:
    """Render a nested ``key:`` mapping (``values:``/``bits:`` typed-decode maps).

    Keys are emitted as integers (sorted numerically) and labels quoted, e.g.::

        values:
          40: "fan1"
          45: "fanMAX"
    """
    if not mapping:
        return [f"{indent}{key}: {{}}"]
    out = [f"{indent}{key}:"]
    try:
        items = sorted(mapping.items(), key=lambda kv: int(kv[0]))
    except (ValueError, TypeError):
        items = list(mapping.items())
    for k, v in items:
        out.append(f"{indent}  {int(k)}: {_format_label(str(v))}")
    return out


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
            # ECU file keys may be mixed-case (e.g. `Unknown-746`) while callers
            # pass the upper-cased key — resolve case-insensitively.
            ecu_def = next((v for k, v in data.items() if str(k).upper() == str(ecu).upper()), None)
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


def _remove_field_line(block: str, field: str, indent: int) -> str:
    """Drop a scalar ``field:`` line at ``indent`` spaces from ``block``."""
    field_re = re.compile(rf"^ {{{indent}}}{re.escape(field)}:")
    return "".join(ln for ln in block.splitlines(keepends=True) if not field_re.match(ln))


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

    Replaces ``field:`` (and any block-scalar or nested list/map continuation)
    at ``indent`` spaces within ``block``; appends the field if absent.
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
            elif (
                rest == ""
                and i < len(lines)
                and (lines[i] == "" or lines[i].startswith(" " * (indent + 1)))
            ):
                # The OLD field is a nested block (list/map, e.g. values:/bits:
                # or a block-style can_bus:) with an empty header rest — skip its
                # deeper-indented body so the whole old block is replaced, not
                # just the header line. Keyed on the old body's shape (not the
                # replacement's) so a block field can be replaced by an inline
                # one (e.g. block can_bus: -> can_bus: [B-CAN, P-CAN]).
                while i < len(lines) and (
                    lines[i] == "" or lines[i].startswith(" " * (indent + 1))
                ):
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
