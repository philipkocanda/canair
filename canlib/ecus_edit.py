"""Surgical, comment-preserving writes to per-ECU definition files (``ecus/``).

Each ECU lives in its own ``ecus/<name>.yaml`` file (keyed by the ECU short
name, carrying ``tx_id`` and an ``identity:`` block). These helpers let the
discovery/identity flows register new ECUs and fill in identity metadata
without clobbering hand-authored edits:

* :func:`register_ecu` — create a new ``ecus/<name>.yaml`` (or merge missing
  identity fields into the existing file for that TX id).
* :func:`set_ecu_fields` — update identity fields on an existing ECU file.
* :func:`append_scan_log` — record a probe outcome under the ECU's ``scan_log:``.

All writes go through :func:`_safe_write`, which re-parses and schema-validates
the file and reverts on failure — a broken edit never persists. Identity fields
are validated against ``canlib/schema/pids_schema.yaml`` (``identity_fields``).
Merges never overwrite existing non-empty values unless ``overwrite=True``.
"""

from __future__ import annotations

from datetime import date as _date
from pathlib import Path

from ruamel.yaml.comments import CommentedMap
from ruamel.yaml.scalarint import HexCapsInt

from .yaml_rt import detect_sequence_indent as _detect_seq
from .yaml_rt import dump as _dump
from .yaml_rt import round_trip_yaml as _yaml

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


class EcusEditError(Exception):
    """Raised when an ECU-file edit cannot be applied safely."""


# ── helpers ───────────────────────────────────────────────────────────────


def tx_key(tx_id: int) -> str:
    """Human-readable display form for a TX id (e.g. ``0x7E0``)."""
    if not isinstance(tx_id, int) or isinstance(tx_id, bool) or tx_id < 0 or tx_id > 0x7FF:
        raise EcusEditError(f"tx_id must be an int in 0x000-0x7FF, got {tx_id!r}")
    return f"0x{tx_id:03X}"


def _hex_tx(tx_id: int) -> HexCapsInt:
    """A hex-rendering integer so ``tx_id`` dumps as ``0x7E0``."""
    return HexCapsInt(tx_id, width=3)


def _resolve_dir(ecus_dir: Path | None) -> Path:
    if ecus_dir is None:
        from .profile import active

        return active().ecus_dir
    return Path(ecus_dir)


def _slug(name: str) -> str:
    return name.strip().lower().replace(" ", "-") + ".yaml"


def _allowed_identity_fields() -> set[str]:
    from .commands.validate import load_schema

    schema = load_schema()
    ident = schema.get("identity_fields", {}) or {}
    return set(ident.get("required", [])) | set(ident.get("optional", []))


def _check_fields(fields: dict) -> None:
    unknown = set(fields) - _allowed_identity_fields()
    if unknown:
        raise EcusEditError(
            f"unknown identity field(s): {', '.join(sorted(unknown))}. "
            f"See identity_fields in canlib/schema/pids_schema.yaml."
        )


def _find_file_by_tx(tx_id: int, ecus_dir: Path) -> tuple[Path | None, str | None]:
    """Locate the ``ecus/<name>.yaml`` file whose ECU has ``tx_id``."""
    y = _yaml()
    for fpath in sorted(ecus_dir.glob("*.yaml")):
        if fpath.name.startswith("_"):
            continue
        try:
            with open(fpath) as f:
                data = y.load(f)
        except Exception:
            continue
        if not isinstance(data, dict):
            continue
        for name, ecu_def in data.items():
            if isinstance(ecu_def, dict) and ecu_def.get("tx_id") == tx_id:
                return fpath, name
    return None, None


def _load_doc(path: Path) -> CommentedMap:
    """Round-trip load an ECU file (or a fresh doc if absent/empty)."""
    y = _yaml()
    data = None
    if path.exists():
        with open(path) as f:
            data = y.load(f)
    if data is None:
        data = CommentedMap()
    if not isinstance(data, dict):
        raise EcusEditError(f"{path} top-level must be a mapping")
    return data


def _merge_fields(entry: dict, updates: dict, overwrite: bool) -> bool:
    """Merge ``updates`` into ``entry``; return True if anything changed."""
    changed = False
    for key, val in updates.items():
        if val is None:
            continue
        cur = entry.get(key)
        if overwrite or cur is None or cur == "":
            if cur != val:
                entry[key] = val
                changed = True
    return changed


def _new_identity(fields: dict) -> CommentedMap:
    ident = CommentedMap()
    for key in CANONICAL_FIELD_ORDER:
        if fields.get(key) is not None:
            ident[key] = fields[key]
    for key, val in fields.items():
        if key not in ident and val is not None:
            ident[key] = val
    return ident


def _safe_write(path: Path, original: str | None, data) -> None:
    """Write ``data``, then re-parse + schema-validate; revert on failure."""
    seq_off = _detect_seq(original or "") or (4, 2)
    with open(path, "w") as f:
        _dump(data, f, sequence=seq_off[0], offset=seq_off[1])
    _invalidate()
    try:
        from .commands.validate import validate_pids_file

        ok, msg = validate_pids_file(path)
        if not ok:
            raise EcusEditError(f"ECU file invalid after edit:\n{msg}")
    except EcusEditError:
        _restore(path, original)
        raise
    except Exception as e:  # pragma: no cover - defensive
        _restore(path, original)
        raise EcusEditError(f"edit failed post-check, reverted: {e}") from e


def _restore(path: Path, original: str | None) -> None:
    if original is None:
        path.unlink(missing_ok=True)
    else:
        path.write_text(original)
    _invalidate()


def _invalidate() -> None:
    from .pids import clear_cache

    clear_cache()


# ── public API ──────────────────────────────────────────────────────────────


def register_ecu(
    tx_id: int,
    name: str | None = None,
    *,
    overwrite: bool = False,
    ecus_dir: Path | None = None,
    **fields,
) -> bool:
    """Register an ECU as ``ecus/<name>.yaml``, or merge into the existing file.

    A new file defaults its name to ``Unknown-<TX>`` when none is given. Existing
    files keep their human-authored identity fields; only missing/empty ones are
    filled unless ``overwrite=True``. ``fields`` are identity fields (see
    ``identity_fields`` in the schema). Returns True if a file was written.
    """
    _check_fields(fields)
    disp = tx_key(tx_id)  # validates range
    ecus_dir = _resolve_dir(ecus_dir)

    fpath, existing_name = _find_file_by_tx(tx_id, ecus_dir)

    if fpath is not None:
        original = fpath.read_text()
        data = _load_doc(fpath)
        ecu_def = data[existing_name]
        if not isinstance(ecu_def, dict):
            raise EcusEditError(f"{fpath.name}/{existing_name} is not a mapping")
        ident = ecu_def.get("identity")
        if not isinstance(ident, dict):
            ident = CommentedMap()
            ecu_def["identity"] = ident
        changed = _merge_fields(ident, fields, overwrite)
        if changed:
            _safe_write(fpath, original, data)
        return changed

    # New file
    ecu_name = name or f"Unknown-{tx_id:03X}"
    ecus_dir.mkdir(parents=True, exist_ok=True)
    fpath = ecus_dir / _slug(ecu_name)
    if fpath.exists():
        raise EcusEditError(f"{fpath.name} already exists but has no tx_id {disp}")
    data = CommentedMap()
    ecu_def = CommentedMap()
    ecu_def["tx_id"] = _hex_tx(tx_id)
    ident = _new_identity(fields)
    if len(ident):
        ecu_def["identity"] = ident
    data[ecu_name] = ecu_def
    _safe_write(fpath, None, data)
    return True


def set_ecu_fields(
    tx_id: int,
    *,
    overwrite: bool = False,
    ecus_dir: Path | None = None,
    **fields,
) -> bool:
    """Update identity fields on an existing ECU file. Returns True if changed.

    Raises :class:`EcusEditError` if no ECU file has ``tx_id`` (use
    :func:`register_ecu` first).
    """
    _check_fields(fields)
    disp = tx_key(tx_id)
    ecus_dir = _resolve_dir(ecus_dir)

    fpath, name = _find_file_by_tx(tx_id, ecus_dir)
    if fpath is None:
        raise EcusEditError(f"ECU {disp} not registered; call register_ecu first")

    original = fpath.read_text()
    data = _load_doc(fpath)
    ecu_def = data[name]
    if not isinstance(ecu_def, dict):
        raise EcusEditError(f"{fpath.name}/{name} is not a mapping")
    ident = ecu_def.get("identity")
    if not isinstance(ident, dict):
        ident = CommentedMap()
        ecu_def["identity"] = ident
    changed = _merge_fields(ident, fields, overwrite)
    if changed:
        _safe_write(fpath, original, data)
    return changed


# scan_log entry fields we accept (mirrors pids_schema scan_log_entry_fields).
_SCAN_LOG_FIELDS = ("service", "range", "date", "hits", "probes", "state", "notes")


def append_scan_log(
    tx_id: int,
    *,
    service=None,
    range=None,
    date=None,
    hits=None,
    probes=None,
    state: str | None = None,
    notes: str | None = None,
    ecus_dir: Path | None = None,
) -> None:
    """Append a probe-outcome entry under the ECU's ``scan_log:`` (date defaults to today)."""
    disp = tx_key(tx_id)
    ecus_dir = _resolve_dir(ecus_dir)

    fpath, name = _find_file_by_tx(tx_id, ecus_dir)
    if fpath is None:
        raise EcusEditError(f"ECU {disp} not registered; call register_ecu first")

    original = fpath.read_text()
    data = _load_doc(fpath)
    ecu_def = data[name]
    if not isinstance(ecu_def, dict):
        raise EcusEditError(f"{fpath.name}/{name} is not a mapping")

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
        "state": state,
        "notes": notes,
    }
    entry = CommentedMap()
    for field in _SCAN_LOG_FIELDS:
        if values[field] is not None:
            entry[field] = values[field]
    ecu_def["scan_log"].append(entry)

    _safe_write(fpath, original, data)
