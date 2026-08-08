"""Parameter / research / identity editors (the reverse-engineering workflow).

Unlike the scanner sections (routines / iocontrol_discoveries), these edit the
hand-authored ``pids:``/``research:``/``identity:`` structures used when
decoding a new PID. The deeper nesting (pids: PID: parameters: PARAM: field)
needs its own indent-anchored locators. Every write is followed by a YAML
re-parse; on any failure the original text is restored so a botched edit never
lands on disk. Builds on the shared primitives in
:mod:`canlib.pids_edit._text`.
"""

from __future__ import annotations

import re
from pathlib import Path

from ._text import (
    PidsEditError,
    _find_ecu_block,
    _format_block_scalar,
    _format_inline_list_field,
    _format_list_field,
    _format_map_field,
    _format_scalar_field,
    _insert_lines,
    _key_token,
    _keyed_block,
    _remove_field_line,
    _replace_field_in_block_at,
    _safe_write,
    _today,
    find_ecu_file,
)

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
    "type",
    "values",
    "bits",
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


def _norm_states(value: object) -> list[str]:
    """Normalize a ``vehicle_states`` value to the canonical UPPERCASE token list.

    Delegates to :func:`canlib.states.parse_states` so the normalization rule
    (strip + upper-case, drop empties, accept a list or comma-string) is shared
    with the runtime readers rather than reimplemented in the text editors.
    """
    from canlib.states import parse_states

    return parse_states(value)


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
        elif key in ("values", "bits"):
            lines.extend(_format_map_field(fld, key, val if isinstance(val, dict) else {}))
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
            joined = ", ".join(_norm_states(val))
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
    type: str | None = None,
    values: dict | None = None,
    bits: dict | None = None,
    pids_dir: Path | None = None,
) -> Path:
    """Add or update one parameter under ``ECU.pids.<PID>.parameters``.

    New parameters are rendered from the provided fields in canonical order.
    Existing parameters have only the *provided* fields replaced in place
    (other fields and formatting are preserved). Creates the ``PID`` block
    and/or ``parameters:`` map if missing, and scaffolds a whole ``pids:``
    section if the ECU has none yet (upsert is the create path — e.g. a
    freshly registered, PID-less ECU). Requires only that the ECU exists.

    The write is verified by a YAML re-parse; on failure the file is restored.
    """
    if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", param_name or ""):
        raise PidsEditError(f"invalid parameter name {param_name!r}")
    if not (expression or "").strip():
        raise PidsEditError("expression must not be empty")

    provided = {
        "expression": expression,
        "type": type,
        "values": values,
        "bits": bits,
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
            # ECU exists but has no pids: section yet (e.g. a freshly registered
            # ECU). upsert is the create path, so scaffold the whole
            # pids: -> PID -> parameters: -> param chain at the end of the ECU
            # block.
            param_lines = _format_param_block(param_name, fields, indent=8)
            block = [
                "  pids:",
                f"    {_key_token(pid_u)}:",
                "      status: active",
                "      parameters:",
                *param_lines,
            ]
            return _insert_lines(text, ecu_start, ecu_end, block)
        _, _, pids_body_start, pids_body_end, _ = pids

        # An inline-empty pids: (``pids: {}`` / ``pids:``) has no block body to
        # append into; rewrite the header to block form and add the PID chain.
        pids_inline = pids[4]
        if pids_body_start >= pids_body_end or pids_inline in ("{}", "{ }"):
            param_lines = _format_param_block(param_name, fields, indent=8)
            chain = "\n".join(
                [
                    "  pids:",
                    f"    {_key_token(pid_u)}:",
                    "      status: active",
                    "      parameters:",
                    *param_lines,
                ]
            )
            return text[: pids[0]] + chain + "\n" + text[pids[1] + 1 :]

        pidb = _keyed_block(text, pid_u, 4, pids_body_start, pids_body_end)
        if not pidb:
            # New PID block appended to the pids: section.
            param_lines = _format_param_block(param_name, fields, indent=8)
            block = [
                f"    {_key_token(pid_u)}:",
                "      status: active",
                "      parameters:",
                *param_lines,
            ]
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
                elif key in ("values", "bits"):
                    v = fields[key]
                    repl = _format_map_field(" " * 10, key, v if isinstance(v, dict) else {})
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


# Boolean param fields a surgical single-field set is allowed to touch.
_PARAM_BOOL_FIELDS = ("verified", "enabled")


def set_param_field(
    ecu_name: str,
    pid: str,
    param_name: str,
    field: str,
    value: bool,
    *,
    pids_dir: Path | None = None,
) -> Path:
    """Set a single boolean field (``verified``/``enabled``) on an existing param.

    Unlike :func:`upsert_parameter`, this rewrites *only* the ``field:`` line and
    leaves every other field — including ``expression`` — byte-for-byte untouched.
    A toggle must not re-render (and thus re-normalize the quoting of) fields the
    user didn't change. The write is verified by a YAML re-parse; on failure the
    original file is restored.
    """
    if field not in _PARAM_BOOL_FIELDS:
        raise PidsEditError(f"field must be one of {_PARAM_BOOL_FIELDS}, got {field!r}")

    fpath = find_ecu_file(ecu_name, pids_dir=pids_dir)
    original = fpath.read_text()
    ecu_key = ecu_name.strip().upper()
    pid_u = str(pid).strip().upper()

    def transform(text: str) -> str:
        ecu_start, ecu_end = _find_ecu_block(text, ecu_name)
        pids = _keyed_block(text, "pids", 2, ecu_start, ecu_end)
        if not pids:
            raise PidsEditError(f"ECU {ecu_name!r} has no pids: section")
        pidb = _keyed_block(text, pid_u, 4, pids[2], pids[3])
        if not pidb:
            raise PidsEditError(f"PID {pid_u!r} not found under {ecu_name!r}")
        params = _keyed_block(text, "parameters", 6, pidb[2], pidb[3])
        if not params:
            raise PidsEditError(f"PID {pid_u!r} has no parameters:")
        existing = _keyed_block(text, param_name, 8, params[2], params[3])
        if not existing:
            raise PidsEditError(f"parameter {param_name!r} not found on {ecu_name} {pid_u}")
        e_start, e_end = existing[0], existing[3]
        block_text = text[e_start:e_end]
        repl = _format_scalar_field(" " * 10, field, value)
        block_text = _replace_field_in_block_at(block_text, field, repl, indent=10)
        return text[:e_start] + block_text + text[e_end:]

    def checker(ecu_def: dict) -> None:
        pids_map = ecu_def.get("pids", {}) or {}
        pdef = next((v for k, v in pids_map.items() if str(k).upper() == pid_u), None)
        params = (pdef or {}).get("parameters") or {}
        pm = params.get(param_name)
        if pm is None:
            raise PidsEditError(f"parameter {param_name!r} missing after edit")
        if bool(pm.get(field)) != bool(value):
            raise PidsEditError(f"{field} mismatch after edit")

    new_text = transform(original)
    _safe_write(fpath, original, new_text, ecu_key, checker)
    return fpath


def rename_parameter(
    ecu_name: str, pid: str, old_name: str, new_name: str, *, pids_dir: Path | None = None
) -> Path:
    """Rename a parameter's key under ``ECU.pids.<PID>.parameters``.

    Only the mapping key is changed; the param's fields and formatting are
    preserved. Fails if ``old_name`` is absent or ``new_name`` already exists.
    Verified by YAML re-parse; the file is restored on failure.
    """
    if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", new_name or ""):
        raise PidsEditError(f"invalid parameter name {new_name!r}")

    fpath = find_ecu_file(ecu_name, pids_dir=pids_dir)
    original = fpath.read_text()
    ecu_key = ecu_name.strip().upper()
    pid_u = str(pid).strip().upper()

    def transform(text: str) -> str:
        e_start, e_end = _find_ecu_block(text, ecu_name)
        pids = _keyed_block(text, "pids", 2, e_start, e_end)
        if not pids:
            raise PidsEditError(f"ECU {ecu_name!r} has no pids: section")
        pidb = _keyed_block(text, pid_u, 4, pids[2], pids[3])
        if not pidb:
            raise PidsEditError(f"PID {pid!r} not found on {ecu_name}")
        params = _keyed_block(text, "parameters", 6, pidb[2], pidb[3])
        if not params:
            raise PidsEditError(f"PID {pid!r} has no parameters:")
        if _keyed_block(text, new_name, 8, params[2], params[3]):
            raise PidsEditError(f"parameter {new_name!r} already exists on {ecu_name} {pid}")
        existing = _keyed_block(text, old_name, 8, params[2], params[3])
        if not existing:
            raise PidsEditError(f"parameter {old_name!r} not found on {ecu_name} {pid}")
        hdr = existing[0]
        renamed = re.sub(
            rf"^( {{8}}){re.escape(old_name)}:",
            rf"\g<1>{new_name}:",
            text[hdr : existing[1] + 1],
            count=1,
        )
        return text[:hdr] + renamed + text[existing[1] + 1 :]

    def checker(ecu_def: dict) -> None:
        pids_map = ecu_def.get("pids", {})
        match = next((v for k, v in pids_map.items() if str(k).upper() == pid_u), None)
        params = (match or {}).get("parameters") or {}
        if new_name not in params:
            raise PidsEditError(f"parameter {new_name!r} missing after rename")
        if old_name in params:
            raise PidsEditError(f"old parameter {old_name!r} still present after rename")

    new_text = transform(original)
    _safe_write(fpath, original, new_text, ecu_key, checker)
    return fpath


def delete_parameter(
    ecu_name: str, pid: str, param_name: str, *, pids_dir: Path | None = None
) -> Path:
    """Remove a parameter block from ``ECU.pids.<PID>.parameters``.

    Fails if the parameter is absent. Verified by YAML re-parse; the file is
    restored on failure.
    """
    fpath = find_ecu_file(ecu_name, pids_dir=pids_dir)
    original = fpath.read_text()
    ecu_key = ecu_name.strip().upper()
    pid_u = str(pid).strip().upper()

    def transform(text: str) -> str:
        e_start, e_end = _find_ecu_block(text, ecu_name)
        pids = _keyed_block(text, "pids", 2, e_start, e_end)
        if not pids:
            raise PidsEditError(f"ECU {ecu_name!r} has no pids: section")
        pidb = _keyed_block(text, pid_u, 4, pids[2], pids[3])
        if not pidb:
            raise PidsEditError(f"PID {pid!r} not found on {ecu_name}")
        params = _keyed_block(text, "parameters", 6, pidb[2], pidb[3])
        if not params:
            raise PidsEditError(f"PID {pid!r} has no parameters:")
        existing = _keyed_block(text, param_name, 8, params[2], params[3])
        if not existing:
            raise PidsEditError(f"parameter {param_name!r} not found on {ecu_name} {pid}")
        # If this is the ONLY parameter under the PID, drop the whole
        # ``parameters:`` block — leaving an empty ``parameters:`` key parses to
        # None and fails the schema ("'parameters' must be a dict").
        sibling = re.compile(r"^ {8}[^\s#]", re.MULTILINE)
        others = [
            m
            for m in sibling.finditer(text, params[2], params[3])
            if not (existing[0] <= m.start() < existing[3])
        ]
        if not others:
            p_start, p_end = params[0], params[3]
            block = text[p_start:p_end]
            trailing = block[len(block.rstrip("\n")) :]
            return text[:p_start] + trailing + text[p_end:]
        start, end = existing[0], existing[3]
        # _keyed_block's body_end absorbs trailing blank lines that visually
        # separate this param from the next sibling. Keep them so removing a
        # param doesn't silently collapse the spacing around neighbours.
        block = text[start:end]
        trailing = block[len(block.rstrip("\n")) :]
        return text[:start] + trailing + text[end:]

    def checker(ecu_def: dict) -> None:
        pids_map = ecu_def.get("pids", {})
        match = next((v for k, v in pids_map.items() if str(k).upper() == pid_u), None)
        params = (match or {}).get("parameters") or {}
        if param_name in params:
            raise PidsEditError(f"parameter {param_name!r} still present after delete")

    new_text = transform(original)
    _safe_write(fpath, original, new_text, ecu_key, checker)
    return fpath


def add_pid(
    ecu_name: str,
    pid: str,
    *,
    status: str = "draft",
    vehicle_states: list | None = None,
    period: int | None = None,
    notes: str | None = None,
    pids_dir: Path | None = None,
) -> Path:
    """Create a new *parameter-less* PID under ``ECU.pids.<PID>``.

    The create path for a bare PID — a discovery placeholder or an identity-only
    page (e.g. a not-yet-decoded ``21F2``) that should be polled/documented
    before any parameter is decoded. Renders ``status:`` (default ``draft`` so it
    is swept/queryable but not shipped to the device) plus any provided
    ``vehicle_states``/``period``/``notes``. Scaffolds the ``pids:`` section if
    the ECU has none yet. Fails if the PID already exists (use
    :func:`upsert_parameter` / :func:`set_pid_status` to edit it).

    The write is verified by a YAML re-parse; on failure the file is restored.
    """
    from canlib.pids import PID_STATUSES

    status = str(status).strip().lower()
    if status not in PID_STATUSES:
        raise PidsEditError(f"status must be one of {PID_STATUSES}, got {status!r}")
    pid_u = str(pid).strip().upper()
    if not re.fullmatch(r"[0-9A-Fa-f]{2,}", pid_u):
        raise PidsEditError(f"invalid PID {pid!r} (expected a hex code like 21F2 or 22B003)")

    def _pid_lines(indent: str) -> list[str]:
        lines = [f"{indent}{_key_token(pid_u)}:", f"{indent}  status: {status}"]
        if period is not None:
            lines.append(f"{indent}  period: {period}")
        if vehicle_states:
            lines.extend(
                _format_inline_list_field(
                    indent + "  ", "vehicle_states", _norm_states(vehicle_states)
                )
            )
        if notes:
            lines.extend(_format_block_scalar(indent + "  ", "notes", str(notes)))
        return lines

    fpath = find_ecu_file(ecu_name, pids_dir=pids_dir)
    original = fpath.read_text()
    ecu_key = ecu_name.strip().upper()

    def transform(text: str) -> str:
        ecu_start, ecu_end = _find_ecu_block(text, ecu_name)
        pids = _keyed_block(text, "pids", 2, ecu_start, ecu_end)
        if not pids:
            # ECU has no pids: section yet — scaffold it with this PID.
            block = ["  pids:", *_pid_lines("    ")]
            return _insert_lines(text, ecu_start, ecu_end, block)
        _, _, pids_body_start, pids_body_end, pids_inline = pids
        if pids_body_start >= pids_body_end or pids_inline in ("{}", "{ }"):
            # Inline-empty pids: — rewrite the header to block form + this PID.
            chain = "\n".join(["  pids:", *_pid_lines("    ")])
            return text[: pids[0]] + chain + "\n" + text[pids[1] + 1 :]
        if _keyed_block(text, pid_u, 4, pids_body_start, pids_body_end):
            raise PidsEditError(
                f"PID {pid_u!r} already exists on {ecu_name} — "
                "use upsert-param / set-pid-status to edit it"
            )
        return _insert_lines(text, pids_body_start, pids_body_end, _pid_lines("    "))

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


def set_pid_variable_length(
    ecu_name: str, pid: str, value: bool, *, pids_dir: Path | None = None
) -> Path:
    """Set (or clear) a PID's ``variable_length:`` flag.

    ``variable_length: true`` documents that the PID legitimately returns
    variable-length responses, so a shorter payload is not a truncated read.
    Passing ``False`` removes the field (the default is fixed-length). Written
    after the PID header; verified by a YAML re-parse, restored on failure.
    """
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
        block_text = _remove_field_line(block_text, "variable_length", indent=6)
        if value:
            lines = block_text.splitlines(keepends=True)
            block_text = lines[0] + "      variable_length: true\n" + "".join(lines[1:])
        return text[:p_hdr] + block_text + text[p_body_end:]

    def checker(ecu_def: dict) -> None:
        pids_map = ecu_def.get("pids", {}) or {}
        pdef = next((v for k, v in pids_map.items() if str(k).upper() == pid_u), None)
        if pdef is None:
            raise PidsEditError(f"PID {pid_u!r} missing after edit")
        got = bool((pdef or {}).get("variable_length", False))
        if got != value:
            raise PidsEditError(f"variable_length mismatch after edit: {got!r} != {value!r}")

    new_text = transform(original)
    _safe_write(fpath, original, new_text, ecu_key, checker)
    return fpath


def set_pid_notes(
    ecu_name: str, pid: str, notes: str | None, *, pids_dir: Path | None = None
) -> Path:
    """Set (or clear) a PID's free-text ``notes:``.

    The PID-level counterpart to :func:`set_identity_field` for ``notes``. A PID's
    header note is the record of what the page *is* and what is known about it, so
    it goes stale as decoding progresses — correcting it needs a surgical, validated
    editor rather than a hand-edit (the one thing the edit-via-tool discipline
    forbids).

    ``notes=None`` (or blank) removes the field. Rendered by the shared note policy
    (:func:`canlib.pids_edit._text._format_block_scalar`): short notes stay inline,
    longer ones become a word-wrapped folded ``>-`` block.

    An existing note is replaced in place, preserving its position. A *new* note is
    inserted before ``parameters:`` — keeping the short metadata (``status:``,
    ``vehicle_states:``) above the prose and the prose above the parameter list,
    which is how the hand-authored files read. Verified by a YAML re-parse; the
    original file is restored on failure.
    """
    fpath = find_ecu_file(ecu_name, pids_dir=pids_dir)
    original = fpath.read_text()
    ecu_key = ecu_name.strip().upper()
    pid_u = str(pid).strip().upper()
    text_val = "" if notes is None else str(notes).strip()
    clearing = not text_val

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
        block = text[p_hdr:p_body_end]

        has_note = re.search(r"^ {6}notes:", block, re.MULTILINE) is not None
        if clearing:
            if not has_note:
                raise PidsEditError(f"PID {pid_u!r} has no notes: to clear")
            # Both _remove_field_line and _replace_field_in_block_at now skip a
            # block scalar's body; using the latter with an empty replacement keeps
            # this path symmetric with the set branch below.
            new_block = _replace_field_in_block_at(block, "notes", [], indent=6)
        else:
            repl = _format_block_scalar(" " * 6, "notes", text_val)
            if has_note:
                new_block = _replace_field_in_block_at(block, "notes", repl, indent=6)
            else:
                lines = block.splitlines(keepends=True)
                at = next(
                    (i for i, ln in enumerate(lines) if re.match(r"^ {6}parameters:", ln)),
                    len(lines),
                )
                new_block = "".join(lines[:at] + [f"{ln}\n" for ln in repl] + lines[at:])
        return text[:p_hdr] + new_block + text[p_body_end:]

    def checker(ecu_def: dict) -> None:
        pids_map = ecu_def.get("pids", {}) or {}
        pdef = next((v for k, v in pids_map.items() if str(k).upper() == pid_u), None)
        if pdef is None:
            raise PidsEditError(f"PID {pid_u!r} missing after edit")
        got = (pdef or {}).get("notes")
        if clearing:
            if got is not None:
                raise PidsEditError(f"PID {pid_u!r} notes still present after clear")
            return
        if got is None:
            raise PidsEditError(f"PID {pid_u!r} notes missing after edit")
        # A folded block scalar collapses newlines to spaces; compare normalized.
        if " ".join(str(got).split()) != " ".join(text_val.split()):
            raise PidsEditError(f"PID {pid_u!r} notes mismatch after edit")

    new_text = transform(original)
    _safe_write(fpath, original, new_text, ecu_key, checker)
    return fpath


def rename_pid(ecu_name: str, old_pid: str, new_pid: str, *, pids_dir: Path | None = None) -> Path:
    """Rename a PID key under ``ECU.pids`` (e.g. ``B002`` -> ``22B002``).

    Only the mapping key is changed; the PID's status, parameters, and formatting
    are preserved. Fails if ``old_pid`` is absent or ``new_pid`` already exists.
    Verified by YAML re-parse; the file is restored on failure.
    """
    old_u = str(old_pid).strip().upper()
    new_u = str(new_pid).strip().upper()
    if not re.fullmatch(r"[0-9A-F]+", new_u):
        raise PidsEditError(f"invalid PID code {new_pid!r} (expected hex, e.g. 22B002)")

    fpath = find_ecu_file(ecu_name, pids_dir=pids_dir)
    original = fpath.read_text()
    ecu_key = ecu_name.strip().upper()

    def transform(text: str) -> str:
        e_start, e_end = _find_ecu_block(text, ecu_name)
        pids = _keyed_block(text, "pids", 2, e_start, e_end)
        if not pids:
            raise PidsEditError(f"ECU {ecu_name!r} has no pids: section")
        if new_u != old_u and _keyed_block(text, new_u, 4, pids[2], pids[3]):
            raise PidsEditError(f"PID {new_u!r} already exists on {ecu_name}")
        existing = _keyed_block(text, old_u, 4, pids[2], pids[3])
        if not existing:
            raise PidsEditError(f"PID {old_pid!r} not found on {ecu_name}")
        hdr = existing[0]
        renamed = re.sub(
            rf"""^( {{4}})(?:"{re.escape(old_u)}"|'{re.escape(old_u)}'|{re.escape(old_u)}):""",
            rf"\g<1>{_key_token(new_u)}:",
            text[hdr : existing[1] + 1],
            count=1,
        )
        return text[:hdr] + renamed + text[existing[1] + 1 :]

    def checker(ecu_def: dict) -> None:
        keys = {str(k).upper() for k in (ecu_def.get("pids") or {})}
        if new_u not in keys:
            raise PidsEditError(f"PID {new_u!r} missing after rename")
        if new_u != old_u and old_u in keys:
            raise PidsEditError(f"old PID {old_u!r} still present after rename")

    new_text = transform(original)
    _safe_write(fpath, original, new_text, ecu_key, checker)
    return fpath


def delete_pid(ecu_name: str, pid: str, *, pids_dir: Path | None = None) -> Path:
    """Remove an entire PID block from ``ECU.pids`` (header, status, parameters).

    Fails if the PID is absent. Removing the ECU's *only* PID drops the whole
    ``pids:`` section (leaving an identity-only ECU) rather than an invalid empty
    mapping. Verified by YAML re-parse; the file is restored on failure.
    """
    fpath = find_ecu_file(ecu_name, pids_dir=pids_dir)
    original = fpath.read_text()
    ecu_key = ecu_name.strip().upper()
    pid_u = str(pid).strip().upper()

    def transform(text: str) -> str:
        e_start, e_end = _find_ecu_block(text, ecu_name)
        pids = _keyed_block(text, "pids", 2, e_start, e_end)
        if not pids:
            raise PidsEditError(f"ECU {ecu_name!r} has no pids: section")
        existing = _keyed_block(text, pid_u, 4, pids[2], pids[3])
        if not existing:
            raise PidsEditError(f"PID {pid!r} not found on {ecu_name}")
        # If this is the only PID, drop the whole `pids:` section so the ECU
        # becomes identity-only (an empty `pids:` is not a valid mapping); a
        # PID header is the only thing at 4-space indent under pids:.
        pid_headers = re.findall(r"^ {4}[^\s#]", text[pids[2] : pids[3]], re.MULTILINE)
        if len(pid_headers) <= 1:
            start, end = pids[0], pids[3]
        else:
            start, end = existing[0], existing[3]
        # body_end absorbs trailing blank lines separating this block from the
        # next sibling; keep them so removal doesn't collapse spacing.
        block = text[start:end]
        trailing = block[len(block.rstrip("\n")) :]
        return text[:start] + trailing + text[end:]

    def checker(ecu_def: dict) -> None:
        if any(str(k).upper() == pid_u for k in (ecu_def.get("pids") or {})):
            raise PidsEditError(f"PID {pid_u!r} still present after delete")

    new_text = transform(original)
    _safe_write(fpath, original, new_text, ecu_key, checker)
    return fpath


def _replace_param_field_line(indent: str, key: str, value) -> str:
    """One rendered scalar/bool line for a param field (used on existing blocks)."""
    return _format_scalar_field(indent, key, value)


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


# ── Identity field editor ─────────────────────────────────────────────────────
#
# The ``identity:`` block (part number, versions, protocol, notes, …) is normally
# populated by ``canair discover``/``identity`` from decoded DIDs, but a few
# fields — notably free-text ``notes`` and the human ``description`` — are curated
# by hand. This editor keeps those surgical/validated too, so nothing has to be
# hand-edited in the YAML.


def set_identity_field(
    ecu_name: str,
    field: str,
    value: str,
    *,
    pids_dir: Path | None = None,
) -> Path:
    """Set a single field under ``ECU.identity`` (e.g. ``notes``/``description``).

    Adds the field if missing, replaces it in place if present. ``notes`` is
    rendered as a folded block scalar (multi-line safe); all other fields are
    written as quoted scalars. The write is verified by a YAML re-parse; on any
    failure the original file is restored.
    """
    if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", field or ""):
        raise PidsEditError(f"invalid identity field name {field!r}")
    if value is None or not str(value).strip():
        raise PidsEditError("value must not be empty")

    fpath = find_ecu_file(ecu_name, pids_dir=pids_dir)
    original = fpath.read_text()
    ecu_key = ecu_name.strip().upper()

    def transform(text: str) -> str:
        ecu_start, ecu_end = _find_ecu_block(text, ecu_name)
        identity = _keyed_block(text, "identity", 2, ecu_start, ecu_end)
        if not identity:
            raise PidsEditError(f"ECU {ecu_name!r} has no identity: section")
        _, _, id_body_start, id_body_end, _ = identity
        block = text[id_body_start:id_body_end]
        if field == "notes":
            repl = _format_block_scalar(" " * 4, "notes", str(value))
        else:
            repl = _format_scalar_field(" " * 4, field, str(value))
        new_block = _replace_field_in_block_at(block, field, repl, indent=4)
        return text[:id_body_start] + new_block + text[id_body_end:]

    def checker(ecu_def: dict) -> None:
        identity = ecu_def.get("identity")
        if not isinstance(identity, dict):
            raise PidsEditError("identity: section missing after edit")
        got = identity.get(field)
        if got is None:
            raise PidsEditError(f"identity.{field} missing after edit")
        # Folded block scalars collapse newlines to spaces; compare normalized.
        if " ".join(str(got).split()) != " ".join(str(value).split()):
            raise PidsEditError(f"identity.{field} mismatch after edit")

    new_text = transform(original)
    _safe_write(fpath, original, new_text, ecu_key, checker)
    return fpath


def remove_identity_field(
    ecu_name: str,
    field: str,
    *,
    pids_dir: Path | None = None,
) -> Path:
    """Remove a single field from ``ECU.identity`` (the inverse of :func:`set_identity_field`).

    Drops the field and any block-scalar/nested continuation it owns. Raises if
    the field isn't present, so a typo is a loud failure rather than a silent
    no-op. The write is verified by a YAML re-parse; on any failure the original
    file is restored.
    """
    if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", field or ""):
        raise PidsEditError(f"invalid identity field name {field!r}")

    fpath = find_ecu_file(ecu_name, pids_dir=pids_dir)
    original = fpath.read_text()
    ecu_key = ecu_name.strip().upper()

    def transform(text: str) -> str:
        ecu_start, ecu_end = _find_ecu_block(text, ecu_name)
        identity = _keyed_block(text, "identity", 2, ecu_start, ecu_end)
        if not identity:
            raise PidsEditError(f"ECU {ecu_name!r} has no identity: section")
        id_start, _, id_body_start, id_body_end, _ = identity
        existing = _keyed_block(text, field, 4, id_body_start, id_body_end)
        if not existing:
            raise PidsEditError(f"ECU {ecu_name!r} has no identity.{field}")
        # If this is the ONLY field, drop the whole ``identity:`` block — an
        # empty ``identity:`` key parses to None and reads as "no identity".
        sibling = re.compile(r"^ {4}[^\s#]", re.MULTILINE)
        others = [
            m
            for m in sibling.finditer(text, id_body_start, id_body_end)
            if not (existing[0] <= m.start() < existing[3])
        ]
        if not others:
            block = text[id_start:id_body_end]
            trailing = block[len(block.rstrip("\n")) :]
            return text[:id_start] + trailing + text[id_body_end:]
        block = text[id_body_start:id_body_end]
        new_block = _replace_field_in_block_at(block, field, [], indent=4)
        return text[:id_body_start] + new_block + text[id_body_end:]

    def checker(ecu_def: dict) -> None:
        identity = ecu_def.get("identity")
        if identity is None:
            return  # the field was the last one; the whole block is gone
        if not isinstance(identity, dict):
            raise PidsEditError("identity: section malformed after edit")
        if field in identity:
            raise PidsEditError(f"identity.{field} still present after edit")

    new_text = transform(original)
    _safe_write(fpath, original, new_text, ecu_key, checker)
    return fpath


# ── Top-level list-field editor ───────────────────────────────────────────────
#
# `can_bus` is a top-level ECU field (a sibling of tx_id/identity), a list of
# physical CAN bus segment codes. It is curated by hand — no DID reads it — so it
# gets a surgical/validated editor like the other hand-curated fields.


def set_can_bus(
    ecu_name: str,
    codes: list[str],
    *,
    pids_dir: Path | None = None,
) -> Path:
    """Set the top-level ``can_bus:`` list on an ECU (physical bus segment codes).

    Renders a flow (inline) list (``can_bus: [B-CAN, P-CAN]``) — short and
    readable for the handful of segment codes. Adds the field if missing,
    replaces it in place if present. The write is verified by a YAML re-parse;
    on any failure the original file is restored.
    """
    cleaned = [str(c).strip() for c in codes if str(c).strip()]
    if not cleaned:
        raise PidsEditError("can_bus must have at least one code")

    fpath = find_ecu_file(ecu_name, pids_dir=pids_dir)
    original = fpath.read_text()
    ecu_key = ecu_name.strip().upper()

    def transform(text: str) -> str:
        ecu_start, ecu_end = _find_ecu_block(text, ecu_name)
        # The ECU block body is everything after the `ECU:` header line; top-level
        # fields (tx_id/identity/can_bus/…) live at 2-space indent within it.
        header_end = text.find("\n", ecu_start)
        body_start = header_end + 1
        block = text[body_start:ecu_end]
        repl = _format_inline_list_field(" " * 2, "can_bus", cleaned)
        if re.search(r"^ {2}can_bus:", block, re.MULTILINE):
            new_block = _replace_field_in_block_at(block, "can_bus", repl, indent=2)
            return text[:body_start] + new_block + text[ecu_end:]
        # Absent — insert right after the `tx_id:` line (its natural home), so it
        # doesn't land at the very end of a long ECU block.
        tx_m = re.search(r"^ {2}tx_id:.*$", block, re.MULTILINE)
        insert_at = body_start + (tx_m.end() + 1 if tx_m else 0)
        payload = "".join(ln + "\n" for ln in repl)
        return text[:insert_at] + payload + text[insert_at:]

    def checker(ecu_def: dict) -> None:
        got = ecu_def.get("can_bus")
        if got != cleaned:
            raise PidsEditError(f"can_bus mismatch after edit (got {got!r})")

    new_text = transform(original)
    _safe_write(fpath, original, new_text, ecu_key, checker)
    return fpath


def _normalize_iocontrol_range(spec: str) -> str:
    """Normalize/validate a ``"START-END"`` hex range to canonical upper hex.

    Raises :class:`PidsEditError` on a malformed range so a bad CLI arg is caught
    before the file is touched.
    """
    parts = str(spec).strip().split("-")
    if len(parts) != 2:
        raise PidsEditError(f"range '{spec}' must be 'START-END' (e.g. B000-BFFF)")
    try:
        lo = int(parts[0], 16)
        hi = int(parts[1], 16)
    except ValueError as e:
        raise PidsEditError(f"range '{spec}' has non-hex bounds") from e
    if not 0 <= lo <= 0xFFFF or not 0 <= hi <= 0xFFFF:
        raise PidsEditError(f"range '{spec}' bounds must be within 0000-FFFF")
    if lo > hi:
        raise PidsEditError(f"range '{spec}' start must be <= end")
    return f"{lo:04X}-{hi:04X}"


def set_iocontrol_scan_ranges(
    ecu_name: str,
    ranges: list[str],
    *,
    pids_dir: Path | None = None,
) -> Path:
    """Set the top-level ``iocontrol_scan_ranges:`` list on an ECU.

    A curated list of ``"START-END"`` hex DID ranges the 0x2F scanner sweeps on
    this ECU (the make-neutral replacement for the old hardcoded HK zones).
    Renders a flow (inline) list; adds the field if missing, replaces it in
    place if present. Verified by a YAML re-parse; restores the original on any
    failure.
    """
    cleaned = [_normalize_iocontrol_range(r) for r in ranges if str(r).strip()]
    if not cleaned:
        raise PidsEditError("iocontrol_scan_ranges must have at least one range")

    fpath = find_ecu_file(ecu_name, pids_dir=pids_dir)
    original = fpath.read_text()
    ecu_key = ecu_name.strip().upper()
    field = "iocontrol_scan_ranges"

    def transform(text: str) -> str:
        ecu_start, ecu_end = _find_ecu_block(text, ecu_name)
        header_end = text.find("\n", ecu_start)
        body_start = header_end + 1
        block = text[body_start:ecu_end]
        repl = _format_inline_list_field(" " * 2, field, cleaned)
        if re.search(rf"^ {{2}}{field}:", block, re.MULTILINE):
            new_block = _replace_field_in_block_at(block, field, repl, indent=2)
            return text[:body_start] + new_block + text[ecu_end:]
        # Absent — insert right after the `tx_id:` line (its natural home).
        tx_m = re.search(r"^ {2}tx_id:.*$", block, re.MULTILINE)
        insert_at = body_start + (tx_m.end() + 1 if tx_m else 0)
        payload = "".join(ln + "\n" for ln in repl)
        return text[:insert_at] + payload + text[insert_at:]

    def checker(ecu_def: dict) -> None:
        got = ecu_def.get(field)
        if got != cleaned:
            raise PidsEditError(f"{field} mismatch after edit (got {got!r})")

    new_text = transform(original)
    _safe_write(fpath, original, new_text, ecu_key, checker)
    return fpath


# Canonical field order for a rendered `wake:` block (matches pids_schema.yaml).
WAKE_FIELD_ORDER = (
    "method",
    "prime_pid",
    "attempts",
    "interval_ms",
    "sleep_timer_ms",
    "session_mode",
    "notes",
)


def set_wake(
    ecu_name: str,
    fields: dict,
    *,
    pids_dir: Path | None = None,
) -> Path:
    """Set (or replace) the top-level ``wake:`` block on an ECU.

    ``fields`` is the wake mapping (``method`` required; ``prime_pid``/
    ``attempts``/``interval_ms``/``sleep_timer_ms``/``session_mode``/``notes``
    optional). Rendered as a nested block at 2-space indent, keys in the
    canonical schema order. Adds the block if missing (right after ``can_bus:``/
    ``vehicle_states:``/``tx_id:``), replaces it in place if present. The write is
    verified by a YAML re-parse; on any failure the original file is restored.
    """
    method = str(fields.get("method", "")).strip()
    if not method:
        raise PidsEditError("wake requires a 'method'")

    # Render in canonical order, dropping keys not supplied.
    lines: list[str] = ["  wake:"]
    _int_keys = {"attempts", "interval_ms", "sleep_timer_ms"}
    for key in WAKE_FIELD_ORDER:
        if key not in fields or fields[key] is None:
            continue
        val = fields[key]
        if key == "notes":
            lines.extend(_format_block_scalar(" " * 4, key, str(val)))
        elif key in _int_keys:
            # Bare integer (not quoted) so the schema's int check passes.
            lines.append(f"    {key}: {int(val)}")
        else:
            lines.append(_format_scalar_field(" " * 4, key, val))

    fpath = find_ecu_file(ecu_name, pids_dir=pids_dir)
    original = fpath.read_text()
    ecu_key = ecu_name.strip().upper()

    def transform(text: str) -> str:
        ecu_start, ecu_end = _find_ecu_block(text, ecu_name)
        header_end = text.find("\n", ecu_start)
        body_start = header_end + 1
        block = text[body_start:ecu_end]
        if re.search(r"^ {2}wake:", block, re.MULTILINE):
            new_block = _replace_field_in_block_at(block, "wake", lines, indent=2)
            return text[:body_start] + new_block + text[ecu_end:]
        # Absent — insert after can_bus:/vehicle_states:/tx_id: (whichever is
        # last present), so it sits with the other top-level ECU fields.
        anchor_end = body_start
        for anchor in ("vehicle_states", "can_bus", "tx_id"):
            m = re.search(rf"^ {{2}}{anchor}:.*$", block, re.MULTILINE)
            if m:
                anchor_end = body_start + m.end() + 1
                break
        payload = "".join(ln + "\n" for ln in lines)
        return text[:anchor_end] + payload + text[anchor_end:]

    def checker(ecu_def: dict) -> None:
        got = ecu_def.get("wake")
        if not isinstance(got, dict):
            raise PidsEditError("wake: block missing after edit")
        if got.get("method") != method:
            raise PidsEditError(f"wake.method mismatch after edit (got {got.get('method')!r})")

    new_text = transform(original)
    _safe_write(fpath, original, new_text, ecu_key, checker)
    return fpath


def _find_research_block(text: str, ecu_name: str):
    """Return the ``_keyed_block`` tuple for ``ECU_NAME``'s ``research:`` section.

    Raises ``PidsEditError`` if the ECU has no ``research:`` section.
    """
    ecu_start, ecu_end = _find_ecu_block(text, ecu_name)
    research = _keyed_block(text, "research", 2, ecu_start, ecu_end)
    if not research:
        raise PidsEditError(f"ECU {ecu_name!r} has no research: section")
    return research


def _research_item_spans(text: str, research_block) -> list[tuple[int, int]]:
    """Return ``[(start, end), ...]`` spans of every item in a ``research:`` list."""
    _, _, r_body_start, r_body_end, _ = research_block
    item_re = re.compile(r"^ {4}- ", re.MULTILINE)
    starts = [r_body_start + m.start() for m in item_re.finditer(text[r_body_start:r_body_end])]
    return [(s, starts[i + 1] if i + 1 < len(starts) else r_body_end) for i, s in enumerate(starts)]


def _match_research_items(
    text: str, research_block, target: str, type: str | None = None
) -> list[tuple[int, int]]:
    """Return the ``research:`` item spans matching ``target`` (and ``type``, if given).

    Spans are returned in list order.
    """
    target_re = re.compile(r"^ {4,6}(?:- )?target:[ \t]*(.*)$", re.MULTILINE)
    type_re = re.compile(r"^ {4,6}(?:- )?type:[ \t]*(.*)$", re.MULTILINE)
    target_norm = str(target).strip().strip('"').strip("'")

    matches = []
    for s, e in _research_item_spans(text, research_block):
        item = text[s:e]
        tm = target_re.search(item)
        if not tm or tm.group(1).strip().strip('"').strip("'") != target_norm:
            continue
        if type is not None:
            ty = type_re.search(item)
            if not ty or ty.group(1).strip().strip('"').strip("'") != type:
                continue
        matches.append((s, e))
    return matches


def _select_research_match(
    target: str, type: str | None, matches: list[tuple[int, int]], *, index: int | None = None
) -> tuple[int, int]:
    """Apply the shared "refuse rather than guess" selector policy.

    ``matches`` is the list of ``(start, end)`` spans already narrowed by
    ``target`` (and ``type``, if given). Real profiles routinely carry several
    research items with the same ``(type, target)`` as a signal is worked
    byte-by-byte, so ``type`` alone does not guarantee uniqueness — ``index``
    (0-based, within this already-narrowed group) is the final disambiguator.
    """
    type_suffix = f" and type {type!r}" if type else ""
    if not matches:
        raise PidsEditError(f"no research item with target {target!r}" + type_suffix)
    if index is not None:
        if not (0 <= index < len(matches)):
            raise PidsEditError(
                f"index {index} out of range for target {target!r}{type_suffix} "
                f"({len(matches)} matching item{'s' if len(matches) != 1 else ''})"
            )
        return matches[index]
    if len(matches) > 1:
        raise PidsEditError(
            f"ambiguous target {target!r} ({len(matches)} matches); pass type= or index="
        )
    return matches[0]


def _research_list_index(text: str, research_block, span: tuple[int, int]) -> int:
    """Ordinal position of ``span`` among ALL items in the research: list.

    Lets a caller build a positionally-robust post-write checker: even when
    several items share the same ``(type, target)``, checking
    ``research[list_index]`` verifies the *specific* item that was edited,
    rather than just "some item with this target/type looks right".
    """
    spans = _research_item_spans(text, research_block)
    return next(i for i, (s, _e) in enumerate(spans) if s == span[0])


def set_research_status(
    ecu_name: str,
    target: str,
    status: str,
    *,
    type: str | None = None,
    index: int | None = None,
    pids_dir: Path | None = None,
) -> Path:
    """Update the ``status:`` of the research item matching ``target`` (and ``type``).

    Also refreshes the item's ``updated`` timestamp to today's date so status
    transitions are dated automatically.

    Selector policy: matches are narrowed by ``target`` (exact) and, if given,
    ``type``. When more than one item still shares that ``target``/``type``
    pair (common — a signal is often worked over several ``decode`` passes),
    pass ``index`` (0-based, within the narrowed group) to pick one; the group
    order matches the item order in the ``research:`` list. Raises
    ``PidsEditError`` if no item matches, ``index`` is out of range, or the
    match is still ambiguous.
    """
    fpath = find_ecu_file(ecu_name, pids_dir=pids_dir)
    original = fpath.read_text()
    ecu_key = ecu_name.strip().upper()
    target_norm = str(target).strip().strip('"').strip("'")
    today = _today()

    research_block = _find_research_block(original, ecu_name)
    matches = _match_research_items(original, research_block, target, type)
    s, e = _select_research_match(target, type, matches, index=index)
    list_index = _research_list_index(original, research_block, (s, e))

    def transform(text: str) -> str:
        item = text[s:e]
        new_item = _replace_field_in_block_at(item, "status", f"      status: {status}", indent=6)
        new_item = _replace_field_in_block_at(
            new_item, "updated", f'      updated: "{today}"', indent=6
        )
        return text[:s] + new_item + text[e:]

    def checker(ecu_def: dict) -> None:
        research = ecu_def.get("research") or []
        if list_index >= len(research):
            raise PidsEditError("status not applied after edit")
        item = research[list_index]
        ok = (
            item.get("target") == target_norm
            and item.get("status") == status
            and item.get("updated") == today
            and (type is None or item.get("type") == type)
        )
        if not ok:
            raise PidsEditError("status not applied after edit")

    new_text = transform(original)
    _safe_write(fpath, original, new_text, ecu_key, checker)
    return fpath


def delete_research_entry(
    ecu_name: str,
    target: str,
    *,
    type: str | None = None,
    index: int | None = None,
    pids_dir: Path | None = None,
) -> Path:
    """Remove an item from the ECU's ``research:`` list.

    A lead is dropped once it's a dead end, superseded, or folded into another
    entry — previously the only way to do this was hand-editing the YAML,
    which the edit-via-tool discipline forbids. Selector policy (``type``/
    ``index``) mirrors :func:`set_research_status`.

    Drops the whole ``research:`` section if this was the last remaining item
    (an empty ``research:`` key parses to ``None`` and fails the schema).
    Verified by YAML re-parse; the file is restored on failure.
    """
    fpath = find_ecu_file(ecu_name, pids_dir=pids_dir)
    original = fpath.read_text()
    ecu_key = ecu_name.strip().upper()
    target_norm = str(target).strip().strip('"').strip("'")

    research_block = _find_research_block(original, ecu_name)
    matches = _match_research_items(original, research_block, target, type)
    s, e = _select_research_match(target, type, matches, index=index)
    pre_count = len(matches)

    def transform(text: str) -> str:
        r_start, _, _r_body_start, r_body_end, _ = research_block
        others = [span for span in _research_item_spans(text, research_block) if span[0] != s]
        if not others:
            # Last item — drop the whole research: block, else an empty
            # ``research:`` key parses to None and fails the schema.
            block = text[r_start:r_body_end]
            trailing = block[len(block.rstrip("\n")) :]
            return text[:r_start] + trailing + text[r_body_end:]
        block = text[s:e]
        trailing = block[len(block.rstrip("\n")) :]
        return text[:s] + trailing + text[e:]

    def checker(ecu_def: dict) -> None:
        research = ecu_def.get("research") or []
        post_count = sum(
            1
            for item in research
            if str(item.get("target")) == target_norm and (type is None or item.get("type") == type)
        )
        if post_count != pre_count - 1:
            raise PidsEditError("research entry still present after delete")

    new_text = transform(original)
    _safe_write(fpath, original, new_text, ecu_key, checker)
    return fpath


def set_research_notes(
    ecu_name: str,
    target: str,
    notes: str | None,
    *,
    type: str | None = None,
    index: int | None = None,
    pids_dir: Path | None = None,
) -> Path:
    """Set (or clear) a research item's free-text ``notes:``.

    The research-list counterpart to :func:`set_pid_notes` — lets a lead's
    notes be corrected or extended as work on it progresses, without touching
    ``status``/other fields and without hand-editing the YAML. Selector policy
    (``type``/``index``) mirrors :func:`set_research_status`.

    ``notes=None`` (or blank) removes the field. Rendered by the shared note
    policy (:func:`canlib.pids_edit._text._format_block_scalar`): short notes
    stay inline, longer ones become a word-wrapped folded ``>-`` block. An
    existing note is replaced in place; a new one is inserted before whichever
    of ``sources:``/``what_to_test:``/``capture_protocol:`` comes first (or at
    the end of the item if none are present), matching the field order in
    ``RESEARCH_FIELD_ORDER``.
    """
    fpath = find_ecu_file(ecu_name, pids_dir=pids_dir)
    original = fpath.read_text()
    ecu_key = ecu_name.strip().upper()
    target_norm = str(target).strip().strip('"').strip("'")
    text_val = "" if notes is None else str(notes).strip()
    clearing = not text_val

    research_block = _find_research_block(original, ecu_name)
    matches = _match_research_items(original, research_block, target, type)
    s, e = _select_research_match(target, type, matches, index=index)
    list_index = _research_list_index(original, research_block, (s, e))

    def transform(text: str) -> str:
        item = text[s:e]
        has_note = re.search(r"^ {6}notes:", item, re.MULTILINE) is not None
        if clearing:
            if not has_note:
                raise PidsEditError(f"research item {target_norm!r} has no notes: to clear")
            new_item = _replace_field_in_block_at(item, "notes", [], indent=6)
        else:
            repl = _format_block_scalar(" " * 6, "notes", text_val)
            if has_note:
                new_item = _replace_field_in_block_at(item, "notes", repl, indent=6)
            else:
                lines = item.splitlines(keepends=True)
                anchor = next(
                    (
                        i
                        for i, ln in enumerate(lines)
                        if re.match(r"^ {6}(sources|what_to_test|capture_protocol):", ln)
                    ),
                    len(lines),
                )
                new_item = "".join(lines[:anchor] + [f"{ln}\n" for ln in repl] + lines[anchor:])
        return text[:s] + new_item + text[e:]

    def checker(ecu_def: dict) -> None:
        research = ecu_def.get("research") or []
        if list_index >= len(research):
            raise PidsEditError(f"research item {target_norm!r} missing after edit")
        item = research[list_index]
        if str(item.get("target")) != target_norm or (
            type is not None and item.get("type") != type
        ):
            raise PidsEditError(f"research item {target_norm!r} missing after edit")
        got = item.get("notes")
        if clearing:
            if got is not None:
                raise PidsEditError("notes still present after clear")
            return
        if got is None:
            raise PidsEditError("notes missing after edit")
        if " ".join(str(got).split()) != " ".join(text_val.split()):
            raise PidsEditError("notes mismatch after edit")

    new_text = transform(original)
    _safe_write(fpath, original, new_text, ecu_key, checker)
    return fpath
