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
    _format_list_field,
    _format_map_field,
    _format_scalar_field,
    _insert_lines,
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
                f"    {pid_u}:",
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
                    f"    {pid_u}:",
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
            rf"^( {{4}}){re.escape(old_u)}:",
            rf"\g<1>{new_u}:",
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
