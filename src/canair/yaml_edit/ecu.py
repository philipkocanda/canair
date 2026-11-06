"""Surgical, comment-preserving writes to per-ECU definition files (``ecus/``).

Each ECU lives in its own ``ecus/<name>.yaml`` file (keyed by the ECU short
name, carrying ``tx_id`` and an ``identity:`` block). These helpers let the
discovery/identity flows register new ECUs and fill in identity metadata
without clobbering hand-authored edits:

* :func:`register_ecu` — create a new ``ecus/<name>.yaml`` (or merge missing
  identity fields into the existing file for that TX id).
* :func:`set_ecu_fields` — update identity fields on an existing ECU file.
* :func:`append_scan_log` — record a probe outcome under the ECU's ``scan_log:``.

All writes go through :func:`canair.yaml_edit.doc.edit_doc`, which re-parses and
schema-validates the file and reverts on failure — a broken edit never persists. Identity fields
are validated against ``src/canair/schema/pids_schema.yaml`` (``identity_fields``).
Merges never overwrite existing non-empty values unless ``overwrite=True``.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from datetime import date as _date
from pathlib import Path

from ruamel.yaml.comments import CommentedMap, CommentedSeq
from ruamel.yaml.scalarint import HexCapsInt

from canair.core.edit_errors import EditError
from canair.core.yaml_rt import folded as _folded
from canair.ecus.files import find_by_tx
from canair.profile.model import Profile
from canair.states.tokens import parse_states
from canair.uds.addressing import AddressingMode
from canair.validation.schema_fields import load_schema

from .doc import Doc, Target, edit_doc
from .ecufile import clear_cache, schema_validator
from .ruamel_ops import flow_seq, rename_key, state_token

# Free-text ECU fields rendered per the shared note policy (canair.core.yaml_rt): inline when
# short, else a wrapped folded block scalar. A new curated free-text field is added here.
FREE_TEXT_FIELDS = frozenset({"notes"})

# Order used when rendering a brand-new identity block (unknown fields last).
CANONICAL_FIELD_ORDER = (
    "alias",
    "description",
    "part_number",
    "mfg_date",
    "hw_version",
    "sw_version",
    "hw_sw",
    "boot_sw",
    "app_sw",
    "fw_version",
    "firmware",
    "serial",
    "ecu_id",
    "sw_id",
    "calibration",
    "supplier",
    "diag_address",
    "vin",
    "id_protocol",
    "identity_confidence",
    "notes",
)


class EcusEditError(EditError):
    """Raised when an ECU-file edit cannot be applied safely."""


def _flow_states(vehicle_states) -> CommentedSeq | None:
    """Normalize a ``vehicle_states`` list to canonical UPPERCASE, flow-styled.

    Renders as an inline list (``[SLEEP, PLUGGED]``) for readability in the long
    per-ECU files, matching how the ``pids``/``research`` editors write the field.
    Returns ``None`` for an empty/absent value so the caller omits the key.
    """

    toks = parse_states(vehicle_states)
    return flow_seq([state_token(t) for t in toks]) if toks else None


# ── helpers ───────────────────────────────────────────────────────────────


def tx_key(tx_id: int) -> str:
    """Human-readable display form for a CAN id (e.g. ``0x7E0`` / ``0x18DA10F1``).

    Accepts an 11-bit id (``0x000-0x7FF``) or a 29-bit extended id
    (``0x800-0x1FFFFFFF``), rendered 3 or 8 hex digits wide respectively.
    """
    if not isinstance(tx_id, int) or isinstance(tx_id, bool) or tx_id < 0 or tx_id > 0x1FFFFFFF:
        raise EcusEditError(f"tx_id must be an int in 0x000-0x1FFFFFFF, got {tx_id!r}")
    return f"0x{tx_id:08X}" if tx_id > 0x7FF else f"0x{tx_id:03X}"


def _hex_tx(tx_id: int) -> HexCapsInt:
    """A hex-rendering integer so a CAN id dumps as ``0x7E0`` / ``0x18DA10F1``."""
    return HexCapsInt(tx_id, width=8 if tx_id > 0x7FF else 3)


def _slug(name: str) -> str:
    return name.strip().lower().replace(" ", "-") + ".yaml"


def _allowed_identity_fields() -> set[str]:
    schema = load_schema()
    ident = schema.get("identity_fields", {}) or {}
    return set(ident.get("required", [])) | set(ident.get("optional", []))


def _check_fields(fields: dict) -> None:
    unknown = set(fields) - _allowed_identity_fields()
    if unknown:
        raise EcusEditError(
            f"unknown identity field(s): {', '.join(sorted(unknown))}. "
            f"See identity_fields in src/canair/schema/pids_schema.yaml."
        )


def _find_file_by_tx(tx_id: int, ecus_dir: Path) -> tuple[Path | None, str | None]:
    """Locate the ``ecus/<name>.yaml`` file whose ECU has ``tx_id``."""

    return find_by_tx(tx_id, ecus_dir)


def find_ecu_file_by_tx(
    tx_id: int, ecus_dir: Path | None = None, *, profile: Profile
) -> Path | None:
    """Return the ``ecus/<name>.yaml`` path for the ECU with ``tx_id`` (or None).

    The address-keyed counterpart of :func:`canair.yaml_edit.pids.find_ecu_file`
    (which is name-keyed): a scanner knows a CAN address before it knows a name.
    """
    fpath, _name = _find_file_by_tx(tx_id, (ecus_dir or profile.ecus_dir))
    return fpath


def _render_field(key: str, value, key_indent: int):
    """Wrap a free-text field value per the shared note policy; pass others through.

    Free-text fields (``FREE_TEXT_FIELDS``) render inline when short or as a
    wrapped folded block when long; ``key_indent`` is how far the key sits from
    the margin (4 for identity, 6 for a scan_log list-item field).
    """
    if key in FREE_TEXT_FIELDS and isinstance(value, str) and value.strip():
        return _folded(value, key_indent=key_indent, key=key)
    return value


def _merge_fields(entry: dict, updates: dict, overwrite: bool) -> bool:
    """Merge ``updates`` into ``entry``; return True if anything changed."""
    changed = False
    for key, val in updates.items():
        if val is None:
            continue
        cur = entry.get(key)
        if overwrite or cur is None or cur == "":
            if cur != val:
                entry[key] = _render_field(key, val, key_indent=4)
                changed = True
    return changed


def _new_identity(fields: dict) -> CommentedMap:
    ident = CommentedMap()
    for key in CANONICAL_FIELD_ORDER:
        if fields.get(key) is not None:
            ident[key] = _render_field(key, fields[key], key_indent=4)
    for key, val in fields.items():
        if key not in ident and val is not None:
            ident[key] = _render_field(key, val, key_indent=4)
    return ident


ECU = Target(
    key="ecu",
    path=None,
    error=EcusEditError,
    validate=schema_validator(EcusEditError),
    after_write=clear_cache,
)


@contextmanager
def edit_ecu(
    fpath: Path, name: str | None, *, validate: bool = True, create: bool = False
) -> Iterator[Doc]:
    """Open an ECU file; ``doc.section`` is the ECU's own mapping (``doc.data[name]``)."""

    def pick(data):
        ecu_def = data[name]
        if not isinstance(ecu_def, dict):
            raise EcusEditError(f"{fpath.name}/{name} is not a mapping")
        return ecu_def

    with edit_doc(
        ECU, path=fpath, create=create, validate=validate, section_of=pick if name else None
    ) as doc:
        yield doc


# ── public API ──────────────────────────────────────────────────────────────


def register_ecu(
    tx_id: int,
    name: str | None = None,
    *,
    overwrite: bool = False,
    ecus_dir: Path | None = None,
    rx_id: int | None = None,
    mode: str | None = None,
    target_address: int | None = None,
    source_address: int | None = None,
    fc_id: int | None = None,
    profile: Profile,
    **fields,
) -> bool:
    """Register an ECU as ``ecus/<name>.yaml``, or merge into the existing file.

    A new file defaults its name to ``Unknown-<TX>`` when none is given. Existing
    files keep their human-authored identity fields; only missing/empty ones are
    filled unless ``overwrite=True``. ``fields`` are identity fields (see
    ``identity_fields`` in the schema). ``rx_id`` is the optional top-level CAN
    response-address override (for an ECU whose response address doesn't follow
    the profile's ``addressing.rx_offset``). ``mode``/``target_address``/
    ``source_address``/``fc_id`` seed the per-ECU ``addressing:`` block — needed
    to register a 29-bit or extended-addressing ECU in one atomic, valid write
    (see :func:`set_addressing`). Returns True if a file was written.
    """
    _check_fields(fields)
    mode = _normalize_addressing_args(mode, target_address, source_address, fc_id, rx_id)
    disp = tx_key(tx_id)  # validates range
    ecus_dir = ecus_dir or profile.ecus_dir

    fpath, existing_name = _find_file_by_tx(tx_id, ecus_dir)

    if fpath is not None:
        with edit_ecu(fpath, existing_name) as doc:
            ecu_def = doc.section
            ident = ecu_def.get("identity")
            if not isinstance(ident, dict):
                ident = CommentedMap()
                ecu_def["identity"] = ident
            changed = _merge_fields(ident, fields, overwrite)
            # Keep an existing rx_id unless overwrite is requested; the rest of the block merges.
            merge_rx = rx_id if (overwrite or ecu_def.get("rx_id") is None) else None
            changed |= _apply_addressing(
                ecu_def,
                mode=mode,
                target_address=target_address,
                source_address=source_address,
                fc_id=fc_id,
                rx_id=merge_rx,
            )
            doc.discard = not changed
        return changed

    # New file
    ecu_name = name or f"Unknown-{tx_id:03X}"
    fpath = ecus_dir / _slug(ecu_name)
    if fpath.exists():
        raise EcusEditError(f"{fpath.name} already exists but has no tx_id {disp}")
    ecu_def = CommentedMap()
    ecu_def["tx_id"] = _hex_tx(tx_id)
    # Seed addressing (rx_id + block) BEFORE the single validating write, so a
    # 29-bit/extended ECU passes the mode-aware tx_id/rx_id width check.
    _apply_addressing(
        ecu_def,
        mode=mode,
        target_address=target_address,
        source_address=source_address,
        fc_id=fc_id,
        rx_id=rx_id,
    )
    ident = _new_identity(fields)
    if len(ident):
        ecu_def["identity"] = ident
    with edit_ecu(fpath, None, create=True) as doc:
        doc.data[ecu_name] = ecu_def
    return True


def rename_ecu(
    tx_id: int, new_name: str, *, ecus_dir: Path | None = None, profile: Profile
) -> Path:
    """Rename an ECU: rewrite its top-level key and rename ``ecus/<name>.yaml``.

    An ECU's name *is* its single top-level YAML key plus its filename, so a
    rename touches both. The key is rewritten in place through :func:`edit_ecu`
    (re-parsed, schema-validated, reverted on failure — comments and field order
    survive), then the file is moved to ``_slug(new_name)`` if the slug changed.
    The document-leading comment block and every nested comment are preserved.

    Returns the (possibly new) file path. Raises :class:`EcusEditError` if the
    ECU isn't registered, ``new_name`` is empty, the new name is already the
    current one, its slug collides with a *different* ECU file, or the file
    doesn't hold exactly one top-level ECU key.
    """
    new_name = (new_name or "").strip()
    if not new_name:
        raise EcusEditError("new ECU name must be non-empty")

    disp = tx_key(tx_id)
    ecus_dir = ecus_dir or profile.ecus_dir
    fpath, old_name = _find_file_by_tx(tx_id, ecus_dir)
    if fpath is None:
        raise EcusEditError(f"ECU {disp} not registered; nothing to rename")
    if new_name == old_name:
        raise EcusEditError(f"ECU {disp} is already named {new_name!r}")

    new_path = ecus_dir / _slug(new_name)
    if new_path.exists() and new_path.resolve() != fpath.resolve():
        raise EcusEditError(
            f"cannot rename {old_name!r} → {new_name!r}: {new_path.name} already exists"
        )

    # Validate+revert in place first, then move the file (a pure rename can't invalidate a
    # validated document), so a broken edit never leaves a renamed file behind.
    with edit_ecu(fpath, None) as doc:
        keys = list(doc.data.keys())
        if len(keys) != 1 or keys[0] != old_name:
            raise EcusEditError(
                f"{fpath.name} must hold exactly one top-level ECU key ({old_name!r}) to rename"
            )
        rename_key(doc.data, old_name, new_name)
    if new_path.resolve() != fpath.resolve():
        fpath.rename(new_path)
        clear_cache()
    return new_path


def set_ecu_fields(
    tx_id: int,
    *,
    overwrite: bool = False,
    ecus_dir: Path | None = None,
    profile: Profile,
    **fields,
) -> bool:
    """Update identity fields on an existing ECU file. Returns True if changed.

    Raises :class:`EcusEditError` if no ECU file has ``tx_id`` (use
    :func:`register_ecu` first).
    """
    _check_fields(fields)
    disp = tx_key(tx_id)
    ecus_dir = ecus_dir or profile.ecus_dir

    fpath, name = _find_file_by_tx(tx_id, ecus_dir)
    if fpath is None:
        raise EcusEditError(f"ECU {disp} not registered; call register_ecu first")

    with edit_ecu(fpath, name) as doc:
        ident = doc.section.get("identity")
        if not isinstance(ident, dict):
            ident = CommentedMap()
            doc.section["identity"] = ident
        changed = _merge_fields(ident, fields, overwrite)
        doc.discard = not changed
    return changed


# scan_log entry fields we accept (mirrors pids_schema scan_log_entry_fields).
_SCAN_LOG_FIELDS = ("service", "range", "date", "hits", "probes", "vehicle_states", "notes")


def _normalize_addressing_args(
    mode: str | None,
    target_address: int | None,
    source_address: int | None,
    fc_id: int | None,
    rx_id: int | None,
) -> str | None:
    """Validate addressing args; return the canonical mode string (or None).

    Raises :class:`EcusEditError` on an unknown mode or out-of-range byte/id.
    """

    if mode is not None:
        try:
            mode = AddressingMode(str(mode).strip().lower()).value
        except ValueError as e:
            allowed = ", ".join(m.value for m in AddressingMode)
            raise EcusEditError(f"unknown addressing mode {mode!r} (allowed: {allowed})") from e
    for label, byte in (("target_address", target_address), ("source_address", source_address)):
        if byte is not None and not (isinstance(byte, int) and 0 <= byte <= 0xFF):
            raise EcusEditError(f"addressing.{label} must be a byte (0x00-0xFF), got {byte!r}")
    if fc_id is not None:
        tx_key(fc_id)  # reuse the CAN-id range check
    if rx_id is not None:
        tx_key(rx_id)
    return mode


def _apply_addressing(
    ecu_def: CommentedMap,
    *,
    mode: str | None,
    target_address: int | None,
    source_address: int | None,
    fc_id: int | None,
    rx_id: int | None,
) -> bool:
    """Merge addressing fields into an ECU mapping in place; return True if changed.

    ``mode`` must already be a validated canonical string (see
    :func:`_normalize_addressing_args`). Writes ``rx_id`` at the top level and the
    rest under an ``addressing:`` block, creating it if absent.
    """
    changed = False
    if rx_id is not None and ecu_def.get("rx_id") != rx_id:
        ecu_def["rx_id"] = _hex_tx(rx_id)
        changed = True

    updates: list[tuple[str, object]] = []
    if mode is not None:
        updates.append(("mode", mode))
    if target_address is not None:
        updates.append(("target_address", HexCapsInt(target_address, width=2)))
    if source_address is not None:
        updates.append(("source_address", HexCapsInt(source_address, width=2)))
    if fc_id is not None:
        updates.append(("fc_id", _hex_tx(fc_id)))
    if updates:
        block = ecu_def.get("addressing")
        if not isinstance(block, dict):
            block = CommentedMap()
            ecu_def["addressing"] = block
        for key, value in updates:
            if block.get(key) != value:
                block[key] = value
                changed = True
    return changed


def set_addressing(
    tx_id: int,
    *,
    mode: str | None = None,
    target_address: int | None = None,
    source_address: int | None = None,
    fc_id: int | None = None,
    rx_id: int | None = None,
    ecus_dir: Path | None = None,
    profile: Profile,
) -> bool:
    """Set an ECU's ``addressing:`` override block (and/or top-level ``rx_id``).

    Writes only the fields provided (each non-None), merging into any existing
    ``addressing:`` block — the surgical, validated editor for the make-specific
    addressing knobs (extended-11-bit ``target_address``/``source_address``,
    functional-TX ``fc_id``, and the 11-bit/29-bit ``mode``). Byte fields
    (target/source) and the ``fc_id``/``rx_id`` arbitration ids render as hex.
    Returns True if the file changed. Raises :class:`EcusEditError` if the ECU is
    not registered (call :func:`register_ecu` first) or a value is out of range.
    """
    mode = _normalize_addressing_args(mode, target_address, source_address, fc_id, rx_id)

    disp = tx_key(tx_id)
    ecus_dir = ecus_dir or profile.ecus_dir
    fpath, name = _find_file_by_tx(tx_id, ecus_dir)
    if fpath is None:
        raise EcusEditError(f"ECU {disp} not registered; call register_ecu first")

    with edit_ecu(fpath, name) as doc:
        changed = _apply_addressing(
            doc.section,
            mode=mode,
            target_address=target_address,
            source_address=source_address,
            fc_id=fc_id,
            rx_id=rx_id,
        )
        doc.discard = not changed
    return changed


def append_scan_log(
    tx_id: int,
    *,
    service=None,
    range=None,
    date=None,
    hits=None,
    probes=None,
    vehicle_states: list | None = None,
    notes: str | None = None,
    ecus_dir: Path | None = None,
    profile: Profile,
) -> None:
    """Append a probe-outcome entry under the ECU's ``scan_log:`` (date defaults to today)."""
    disp = tx_key(tx_id)
    ecus_dir = ecus_dir or profile.ecus_dir

    fpath, name = _find_file_by_tx(tx_id, ecus_dir)
    if fpath is None:
        raise EcusEditError(f"ECU {disp} not registered; call register_ecu first")

    with edit_ecu(fpath, name) as doc:
        ecu_def = doc.section
        if ecu_def.get("scan_log") is None:
            ecu_def["scan_log"] = []

        values = {
            "service": HexCapsInt(service)
            if isinstance(service, int) and not isinstance(service, bool)
            else service,
            "range": range,
            "date": date if date is not None else _date.today().isoformat(),
            "hits": hits,
            "probes": probes,
            "vehicle_states": _flow_states(vehicle_states),
            "notes": notes,
        }
        entry = CommentedMap()
        for field in _SCAN_LOG_FIELDS:
            if values[field] is not None:
                # scan_log entries are list items: dash at 4, fields at 6.
                entry[field] = _render_field(field, values[field], key_indent=6)
        ecu_def["scan_log"].append(entry)
